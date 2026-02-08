# dev-mesh

**Local development service mesh. Every project gets its own URL.**

dev-mesh gives each project, branch, and worktree a unique HTTPS URL (`myapp-feature.dev.yourdomain.com`) so you can run everything in parallel without port conflicts.

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
        <string>mkdir -p /tmp/caddy-dev /var/lib/caddy/data /var/lib/caddy/config &amp;&amp; chmod 1777 /tmp/caddy-dev &amp;&amp; exec /usr/local/bin/caddy run --config /usr/local/etc/caddy/config.json</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLOUDFLARE_API_TOKEN</key>
        <string>YOUR_TOKEN_HERE</string>
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
- **`HOME`/`XDG_DATA_HOME`/`XDG_CONFIG_HOME`** — required because LaunchDaemons run as root with no `$HOME`. Without these, Caddy fails with "read-only file system" when storing certificates.
- **Startup script** recreates `/tmp/caddy-dev` on boot (macOS clears `/tmp` on reboot).
- This is a **system daemon** (`/Library/LaunchDaemons`), so it runs at boot regardless of which user is logged in. All users can create sockets in `/tmp/caddy-dev` (sticky bit) and register routes via the admin API.

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/com.caddyserver.caddy.plist
```

### 5. Install the dashboard

The dashboard provides an always-on status page at `dev-mesh.dev.yourdomain.com` showing registered services, upstream health, and TLS status. It runs as a dedicated unprivileged user.

```bash
# Create a system user for the dashboard
sudo dscl . -create /Users/_devmesh UniqueID 399
sudo dscl . -create /Users/_devmesh PrimaryGroupID 399
sudo dscl . -create /Users/_devmesh UserShell /usr/bin/false
sudo dscl . -create /Users/_devmesh NFSHomeDirectory /var/empty
sudo dscl . -create /Groups/_devmesh PrimaryGroupID 399

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

Open `https://dev-mesh.dev.yourdomain.com` to see the dashboard.

## Framework Integration

### Phoenix/Elixir

Add to `mix.exs`:

```elixir
{:req, "~> 0.5", only: :dev}
```

Create `lib/my_app/dev_proxy.ex` — see [elixir-integration.md](docs/elixir-integration.md) for the full module.

The integration:
- Checks if Caddy is available on startup
- If yes: binds to Unix socket, registers route, logs the URL
- If no: falls back to standard port binding
- Deregisters on shutdown

### Other frameworks

The registration API is simple HTTP:

```bash
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

# Deregister
curl -X DELETE "http://localhost:2019/id/myapp-feature"
```

Bind your service to `/tmp/caddy-dev/{name}.sock` instead of a port. Most frameworks support Unix sockets:

- **Node/Express:** `server.listen('/tmp/caddy-dev/myapp.sock')`
- **Python/uvicorn:** `uvicorn app:app --uds /tmp/caddy-dev/myapp.sock`
- **Go:** `net.Listen("unix", "/tmp/caddy-dev/myapp.sock")`
- **Ruby/Puma:** `puma -b unix:///tmp/caddy-dev/myapp.sock`

## Naming convention

Subdomains follow `{app}-{branch}` format:

| Worktree | Branch | URL |
|----------|--------|-----|
| `~/code/myapp` | `main` | `myapp.dev.yourdomain.com` |
| `~/code/myapp-feature` | `feature-auth` | `myapp-feature-auth.dev.yourdomain.com` |
| `~/code/myapp-fix` | `fix-123` | `myapp-fix-123.dev.yourdomain.com` |

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

**Stale socket**
`rm /tmp/caddy-dev/*.sock` — crashed processes can leave these behind.

## License

MIT
