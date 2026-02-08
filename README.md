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

### 3. Create Caddy config

Save to `/usr/local/etc/caddy/config.json`:

```json
{
  "admin": {"listen": "localhost:2019"},
  "apps": {
    "dynamic_dns": {
      "domains": {"yourdomain.com": ["dev"]},
      "ip_source": {"source": "interface", "name": "en0"},
      "dns_provider": {
        "name": "cloudflare",
        "api_token": "{env.CLOUDFLARE_API_TOKEN}"
      },
      "check_interval": "5m",
      "versions": {"ipv4": true, "ipv6": true}
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

### 4. Install as system service (macOS)

```bash
sudo mkdir -p /usr/local/etc/caddy /var/log/caddy /tmp/caddy-dev
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
        <string>/usr/local/bin/caddy</string>
        <string>run</string>
        <string>--config</string>
        <string>/usr/local/etc/caddy/config.json</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLOUDFLARE_API_TOKEN</key>
        <string>YOUR_TOKEN_HERE</string>
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

```bash
sudo launchctl load /Library/LaunchDaemons/com.caddyserver.caddy.plist
```

### 5. Verify

```bash
curl http://localhost:2019/config/ | jq .
```

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

**DNS not resolving**  
Wait a minute for propagation, then `dig myapp.dev.yourdomain.com`. Check Caddy logs: `tail -f /var/log/caddy/caddy.log`

**TLS certificate errors**  
Ensure `caddy trust` was run. Check that the Cloudflare token has DNS edit permissions.

**502 Bad Gateway**  
Socket path mismatch. Verify the `dial` path in Caddy matches where your service is listening.

**Stale socket**  
`rm /tmp/caddy-dev/*.sock` — crashed processes can leave these behind.

## License

MIT
