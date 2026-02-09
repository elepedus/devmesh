# devmesh

**Local development service mesh. Every project gets its own URL. Stop port-hopping.**

devmesh gives each project, branch, and worktree a unique HTTPS URL (`myapp-feature.dev.yourdomain.com`) so you can run everything in parallel without port conflicts.

```
┌──────────────────────────────────────────────────────────┐
│  Cloudflare DNS: *.dev.yourdomain.com → your LAN IP      │
│  (auto-updated when you switch networks)                 │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Caddy (system service)                                  │
│  - Wildcard TLS via DNS-01                               │
│  - Dynamic route registration                            │
│  - Reverse proxy to Unix sockets                         │
└──────────────────────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
     app-main.sock    app-feature.sock   other-app.sock
```

## Why

Port 3000 assumes you're running one thing at a time. You're not.

- **Worktrees** — run multiple branches simultaneously
- **Parallel agents** — let AI verify its own work without conflicts
- **Multiple projects** — no more toggling services on and off
- **Mobile testing** — access any service from your phone, instantly

## How it works

1. Caddy runs as a system service, listening on 80/443
2. Services bind to Unix sockets instead of ports
3. On startup, each service registers its socket with Caddy's API
4. Caddy routes `{app}-{branch}.dev.yourdomain.com` → socket
5. Dynamic DNS keeps the wildcard record pointed at your current LAN IP

## Setup

### 1. Build Caddy with required modules

Requires Go 1.23+ (older versions have dylib issues with xcaddy on macOS ARM).

```bash
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
xcaddy build \
  --with github.com/caddy-dns/cloudflare \
  --with github.com/mholt/caddy-dynamicdns

sudo mv caddy /usr/local/bin/
```

### 2. Configure Cloudflare

Create an API token at [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens):
- **Permissions:** Zone → DNS → Edit
- **Zone Resources:** Include → your domain

Add a wildcard A record: `*.dev.yourdomain.com` → any IP (Caddy will update it)

> **Note:** Only create an A record (IPv4). Do not add an AAAA record — the dynamic DNS module will pick up link-local `fe80::` addresses from your interface, which aren't routable from other devices.

### 3. Create Caddy config

Save to `/usr/local/etc/caddy/config.json`:

```json
{
  "admin": {"listen": "localhost:2019"},
  "apps": {
    "dynamic_dns": {
      "domains": {"yourdomain.com": ["*.dev"]},
      "ip_sources": [{"source": "interface", "name": "en0"}],
      "dns_provider": {
        "name": "cloudflare",
        "api_token": "{env.CLOUDFLARE_API_TOKEN}"
      },
      "check_interval": "5m",
      "versions": {"ipv4": true, "ipv6": false}
    },
    "http": {
      "servers": {
        "srv0": {
          "listen": [":443", ":80"],
          "routes": []
        }
      }
    },
    "tls": {
      "certificates": {
        "automate": ["*.dev.yourdomain.com"]
      },
      "automation": {
        "policies": [{
          "subjects": ["*.dev.yourdomain.com"],
          "issuers": [{
            "module": "acme",
            "challenges": {
              "dns": {
                "provider": {
                  "name": "cloudflare",
                  "api_token": "{env.CLOUDFLARE_API_TOKEN}"
                }
              }
            }
          }]
        }]
      }
    }
  }
}
```

Key details:
- **`ip_sources`** (plural, array) — not `ip_source`. The caddy-dynamicdns README may be outdated.
- **`certificates.automate`** — tells Caddy to pre-provision the wildcard cert on startup. Without this, Caddy issues individual per-subdomain certificates.
- **`ipv6: false`** — prevents publishing link-local IPv6 addresses that aren't reachable from other devices.

### 4. Install as system service (macOS)

```bash
sudo mkdir -p /usr/local/etc/caddy /var/log/caddy /var/lib/caddy /tmp/caddy-dev
sudo chmod 1777 /tmp/caddy-dev
sudo caddy trust
```

Create a secrets file for the Cloudflare token (keeps credentials out of the plist and version control):

```bash
sudo tee /usr/local/etc/caddy/env > /dev/null <<'EOF'
export CLOUDFLARE_API_TOKEN=your-token-here
EOF
sudo chmod 600 /usr/local/etc/caddy/env
```

Save to `/Library/LaunchDaemons/com.caddyserver.caddy.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.caddyserver.caddy</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>. /usr/local/etc/caddy/env &amp;&amp; mkdir -p /tmp/caddy-dev /var/lib/caddy/data /var/lib/caddy/config &amp;&amp; chmod 1777 /tmp/caddy-dev &amp;&amp; exec /usr/local/bin/caddy run --config /usr/local/etc/caddy/config.json --resume</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/var/lib/caddy</string>
        <key>XDG_DATA_HOME</key>
        <string>/var/lib/caddy/data</string>
        <key>XDG_CONFIG_HOME</key>
        <string>/var/lib/caddy/config</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/var/log/caddy/caddy.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/caddy/caddy.log</string>
</dict>
</plist>
```

Key details:
- **Secrets file** — The startup script sources `/usr/local/etc/caddy/env` to load the Cloudflare token. This keeps credentials out of the plist (which lives in version control). The file is mode 600 (root-only readable).
- **`--resume`** — Caddy auto-saves its config (including API-added routes) to disk. On restart, `--resume` restores the last config so dynamically registered services don't lose their routes. The `--config` file is used as fallback on first boot. If you edit the config file, use `caddy reload` instead of a restart to pick up changes.
- **`HOME`/`XDG_DATA_HOME`/`XDG_CONFIG_HOME`** — required because LaunchDaemons run as root with no `$HOME`. Without these, Caddy fails with "read-only file system" when storing certificates.
- **Startup script** recreates `/tmp/caddy-dev` on boot (macOS clears `/tmp` on reboot).
- This is a **system daemon** (`/Library/LaunchDaemons`), so it runs at boot regardless of which user is logged in. All users can create sockets in `/tmp/caddy-dev` (sticky bit) and register routes via the admin API.

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/com.caddyserver.caddy.plist
```

### 5. Install the dashboard

The dashboard provides an always-on status page at `devmesh.dev.yourdomain.com` showing registered services, upstream health, and TLS status. It runs as a dedicated unprivileged user.

```bash
# Create a system user for the dashboard (pick an unused UID/GID —
# check with `dscl . -list /Users UniqueID | sort -nk2` first;
# 399 may collide with com.apple.access_ssh on some systems)
sudo dscl . -create /Users/_devmesh UniqueID 399
sudo dscl . -create /Users/_devmesh PrimaryGroupID 399
sudo dscl . -create /Users/_devmesh UserShell /usr/bin/false
sudo dscl . -create /Users/_devmesh NFSHomeDirectory /var/empty
sudo dscl . -create /Groups/_devmesh PrimaryGroupID 399

# Create the log file with correct ownership (the _devmesh user
# cannot create files in /var/log/caddy/)
sudo touch /var/log/caddy/dashboard.log
sudo chown _devmesh /var/log/caddy/dashboard.log

# Install the dashboard script
sudo mkdir -p /usr/local/etc/devmesh
sudo cp dashboard.py /usr/local/etc/devmesh/dashboard.py

# Install and start the service
sudo cp com.devmesh.dashboard.plist /Library/LaunchDaemons/com.devmesh.dashboard.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.devmesh.dashboard.plist
```

### 6. Verify

```bash
curl http://localhost:2019/config/ | jq .
```

Open `https://devmesh.dev.yourdomain.com` to see the dashboard.

## Framework Integration

### Phoenix/Elixir

This repo includes a `dev_mesh` Elixir package that handles all the Caddy integration automatically.

#### 1. Add dependencies

```elixir
# mix.exs
{:dev_mesh, github: "elepedus/devmesh", only: :dev},
{:tidewave, "~> 0.5", only: :dev}
```

#### 2. Create `lib/my_app/dev_proxy.ex`

```elixir
defmodule MyApp.DevProxy do
  use DevMesh,
    route_id: "my-app",
    otp_app: :my_app,
    endpoint: MyAppWeb.Endpoint,
    fallback_port: 4000
end
```

Options:

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `route_id` | yes | | Default subdomain identifier, used when no `.id` file is present |
| `otp_app` | yes | | Application atom (e.g. `:my_app`) |
| `endpoint` | yes | | Phoenix Endpoint module |
| `fallback_port` | yes | | TCP port when Caddy is unavailable |
| `tidewave` | no | `true` | Enable Tidewave Web proxy on port 9833 |
| `caddy_admin` | no | `"http://localhost:2019"` | Caddy admin API URL |
| `sock_dir` | no | `"/tmp/caddy-dev"` | Unix socket directory |
| `tidewave_upstream` | no | `"localhost:9832"` | Tidewave Web address |

#### 3. Add to supervision tree

In `lib/my_app/application.ex`, add `DevProxy` before the Endpoint:

```elixir
children =
  [
    MyAppWeb.Telemetry,
    {Phoenix.PubSub, name: MyApp.PubSub}
  ] ++
    DevMesh.children(MyApp.DevProxy) ++
    [MyAppWeb.Endpoint]
```

#### 4. Configure the endpoint

In `config/dev.exs`, keep the default TCP port binding (this is the fallback when Caddy isn't running):

```elixir
config :my_app, MyAppWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4000],
  ...
```

Use a unique port per project to avoid conflicts (e.g., 4000, 4001, 4002...).

In `config/runtime.exs`, ensure the `http: [port: ...]` line is inside the `if config_env() == :prod` block so it doesn't override the dev config.

In the endpoint module, add the Tidewave plug with `allow_remote_access: true` (required when accessed through a proxy), and override session cookies to `SameSite=None; Secure` in dev mode so Tidewave Web can make cross-port requests:

```elixir
if Code.ensure_loaded?(Tidewave) do
  plug Tidewave, allow_remote_access: true
end

if code_reloading? do
  @session_options Keyword.merge(@session_options, same_site: "None", secure: true)
  # ... existing LiveReloader/CodeReloader plugs
end
```

#### 5. Configure Tidewave Web

In the Tidewave app settings, enable remote access and allow your app origins:

```toml
allow_remote_access = true
allowed_origins = ["https://my-app.dev.yourdomain.com:9833"]
```

#### How it works

The `DevMesh` macro generates a GenServer that:
- Auto-discovers the domain from Caddy's TLS config
- If Caddy is available: switches endpoint to Unix socket, registers HTTPS route, sets endpoint URL for correct WebSocket hostnames
- If Caddy isn't available: leaves TCP port config alone, app works at `http://localhost:PORT`
- Registers a Tidewave Web proxy route on port 9833 with Origin header rewriting
- DELETEs stale routes before registering (handles unclean restarts)
- Deregisters both routes on clean shutdown

### Other frameworks

The registration API is simple HTTP:

```bash
# Deregister any stale route first (ignore errors if none exists)
curl -sf -X DELETE "http://localhost:2019/id/myapp-feature" || true

# Register
curl -X POST "http://localhost:2019/config/apps/http/servers/srv0/routes" \
  -H "Content-Type: application/json" \
  -d '{
    "@id": "myapp-feature",
    "match": [{"host": ["myapp-feature.dev.yourdomain.com"]}],
    "handle": [{
      "handler": "reverse_proxy",
      "upstreams": [{"dial": "unix//tmp/caddy-dev/myapp-feature.sock"}]
    }]
  }'

# Deregister (on shutdown)
curl -X DELETE "http://localhost:2019/id/myapp-feature"
```

**Important:** Always DELETE before POST on startup. If your service crashes or is killed without deregistering, the stale route remains in Caddy. A bare POST creates a duplicate.

Bind your service to `/tmp/caddy-dev/{name}.sock` instead of a port. Most frameworks support Unix sockets:

- **Node/Express:** `server.listen('/tmp/caddy-dev/myapp.sock')`
- **Python/uvicorn:** `uvicorn app:app --uds /tmp/caddy-dev/myapp.sock`
- **Go:** `net.Listen("unix", "/tmp/caddy-dev/myapp.sock")`
- **Ruby/Puma:** `puma -b unix:///tmp/caddy-dev/myapp.sock`

## Worktrees and multiple instances

Each project can have a `.id` file in its root containing the route identity — a single line that becomes the subdomain and socket name. Add `.id` to your `.gitignore` so each worktree can have its own.

```
# main worktree: ~/code/myapp/.id
myapp

# feature worktree: ~/code/myapp-feature/.id
myapp-feature-auth
```

| Worktree | `.id` contents | URL |
|----------|---------------|-----|
| `~/code/myapp` | `myapp` | `myapp.dev.yourdomain.com` |
| `~/code/myapp-feature` | `myapp-feature-auth` | `myapp-feature-auth.dev.yourdomain.com` |
| `~/code/myapp-fix` | `myapp-fix-123` | `myapp-fix-123.dev.yourdomain.com` |

If no `.id` file exists, the `route_id` from the DevProxy module config is used (backwards compatible).

### Automating with worktrunk + mise

[Worktrunk](https://worktrunk.dev/) manages worktree lifecycle. [mise](https://mise.jdx.dev/) manages toolchains and per-directory environment variables. Together they automate the entire flow.

**`.mise.toml`** (committed) — sets toolchain versions, default env vars, and loads per-worktree overrides:

```toml
[tools]
elixir = "1.19"
erlang = "28"
node = "24"

[env]
DATABASE_NAME = "myapp_dev"
TEST_DATABASE_NAME = "myapp_test"
_.file = ".env"
```

**`.config/wt.toml`** (committed) — worktrunk hooks that run when creating/removing worktrees:

```toml
[post-create]
setup = """
echo "myapp-{{ branch | sanitize }}" > .id
cat > .env << EOF
DATABASE_NAME=myapp_{{ branch | sanitize_db }}_dev
TEST_DATABASE_NAME=myapp_{{ branch | sanitize_db }}_test
EOF
cp -cR {{ primary_worktree_path }}/_build . 2>/dev/null || true
cp -cR {{ primary_worktree_path }}/deps . 2>/dev/null || true
mise trust
eval "$(mise activate bash)"
mix deps.get
mix compile
createdb myapp_{{ branch | sanitize_db }}_dev --template=myapp_dev 2>/dev/null || true
mix ecto.migrate 2>/dev/null || true
"""

[pre-remove]
cleanup = """
dropdb myapp_{{ branch | sanitize_db }}_dev --if-exists 2>/dev/null || true
dropdb myapp_{{ branch | sanitize_db }}_test --if-exists 2>/dev/null || true
"""
```

The hook also registers [Tidewave](https://github.com/tidewave-elixir/tidewave) as an MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code), so each worktree's Claude session automatically connects to the right Tidewave instance:

```bash
claude mcp add --transport http --scope project tidewave \
  "https://myapp-{{ branch | sanitize }}.dev.yourdomain.com/tidewave/mcp"
```

This writes to `.mcp.json` in the worktree root. For the main worktree, run this once manually with your default route_id.

**`.env`**, **`.id`**, and **`.mcp.json`** should all be gitignored — they're per-worktree.

The flow:

```
wt switch --create feature-auth
# hook writes .id, .env, .mcp.json
# CoW-copies _build/deps, compiles, creates database from template

mix phx.server
# mise loads .env → DATABASE_NAME set
# devmesh reads .id → https://myapp-feature-auth.dev.yourdomain.com
# Claude Code reads .mcp.json → Tidewave connected

wt remove feature-auth
# hook drops per-worktree databases
```

## Troubleshooting

**EADDRINUSE on port 80/443**
Something else is using those ports. Check with `sudo lsof -i :80`.

**"read-only file system" in Caddy logs**
The `HOME`, `XDG_DATA_HOME`, and `XDG_CONFIG_HOME` environment variables aren't set in the plist. Caddy needs a writable directory for certificate storage.

**DNS not resolving**
Wait a minute for propagation, then `dig myapp.dev.yourdomain.com`. Check Caddy logs: `tail -f /var/log/caddy/caddy.log`

**DNS not resolving on mobile**
Phones may take several minutes to pick up new wildcard records. Try a different subdomain if you suspect caching. Also ensure your phone's DNS isn't filtered by the router (some routers block DNS responses pointing to private IPs as rebinding protection).

**Per-subdomain certs instead of wildcard**
Add `"certificates": {"automate": ["*.dev.yourdomain.com"]}` to the TLS config. Without this, Caddy issues individual certs for each subdomain it encounters.

**TLS certificate errors**
Ensure `caddy trust` was run. Check that the Cloudflare token has DNS edit permissions.

**502 Bad Gateway**
Socket path mismatch. Verify the `dial` path in Caddy matches where your service is listening.

**LiveView/LiveReload WebSocket errors in browser console**
Phoenix defaults to `url: [host: "localhost"]` in `config.exs`. When running behind Caddy at a different hostname, WebSocket connections fail with a hostname mismatch. The DevProxy must also set the `url` config (host, scheme, port) to match the external Caddy URL — see the `configure_endpoint/1` function above.

**Duplicate routes in Caddy**
A service that crashes or is killed without deregistering leaves a stale route. If it restarts and POSTs a new route without first DELETEing the old one, you get duplicates. Always DELETE by `@id` before POSTing on startup. Remove a duplicate manually: `curl -s http://localhost:2019/config/apps/http/servers/srv0/routes | python3 -m json.tool` to find the index, then `curl -X DELETE http://localhost:2019/config/apps/http/servers/srv0/routes/{index}`.

**Tidewave Web not loading through the mesh**
The DevProxy creates a separate Caddy server on port 9833 that proxies to Tidewave Web (localhost:9832). Check that: (1) Tidewave Web is running, (2) the Tidewave app settings have `allow_remote_access = true` and the correct `allowed_origins`, (3) the tidewave server exists in Caddy: `curl http://localhost:2019/config/apps/http/servers/tidewave | python3 -m json.tool`. Note: Caddy returns `200` with `null` body for missing config paths — the `ensure_tidewave_server` check must verify the body is a map, not just status 200.

**Stale socket**
`rm /tmp/caddy-dev/*.sock` — crashed processes can leave these behind.

## License

MIT
