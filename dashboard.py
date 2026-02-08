#!/usr/bin/env python3
"""dev-mesh dashboard — always-on status page for the local service mesh."""

import json
import os
import signal
import socket
import sys
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

CADDY_ADMIN = "http://localhost:2019"
SOCK_DIR = "/tmp/caddy-dev"
SOCK_PATH = os.path.join(SOCK_DIR, "dev-mesh.sock")
ROUTE_ID = "dev-mesh"


def caddy_get(path):
    try:
        with urllib.request.urlopen(f"{CADDY_ADMIN}{path}", timeout=2) as r:
            return json.loads(r.read()) if r.status == 200 else None
    except Exception:
        return None


def caddy_get_text(path):
    try:
        with urllib.request.urlopen(f"{CADDY_ADMIN}{path}", timeout=2) as r:
            return r.read().decode() if r.status == 200 else None
    except Exception:
        return None


def discover_domain():
    """Read domain pattern from Caddy's TLS config."""
    tls = caddy_get("/config/apps/tls/")
    if tls:
        subjects = (tls.get("certificates", {}).get("automate") or
                     [p["subjects"][0] for p in tls.get("automation", {}).get("policies", []) if p.get("subjects")])
        for s in subjects:
            if s.startswith("*."):
                return s[2:]  # e.g. "a2780.lpds.dev"
    return None


def register_with_caddy(domain):
    data = json.dumps({
        "@id": ROUTE_ID,
        "match": [{"host": [f"{ROUTE_ID}.{domain}"]}],
        "handle": [{
            "handler": "reverse_proxy",
            "upstreams": [{"dial": f"unix/{SOCK_PATH}"}]
        }]
    }).encode()
    # Remove existing route first (ignore errors)
    try:
        req = urllib.request.Request(f"{CADDY_ADMIN}/id/{ROUTE_ID}", method="DELETE")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass
    req = urllib.request.Request(
        f"{CADDY_ADMIN}/config/apps/http/servers/srv0/routes",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=2)


def deregister_from_caddy():
    try:
        req = urllib.request.Request(f"{CADDY_ADMIN}/id/{ROUTE_ID}", method="DELETE")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def get_status():
    routes = caddy_get("/config/apps/http/servers/srv0/routes") or []
    upstreams = caddy_get("/reverse_proxy/upstreams") or []
    tls_config = caddy_get("/config/apps/tls/") or {}
    dyn_dns = caddy_get("/config/apps/dynamic_dns/") or {}
    metrics_text = caddy_get_text("/metrics") or ""

    # Parse upstream health from prometheus metrics
    health_map = {}
    for line in metrics_text.splitlines():
        if line.startswith("caddy_reverse_proxy_upstreams_healthy{"):
            try:
                addr = line.split('upstream="')[1].split('"')[0]
                val = float(line.split()[-1])
                health_map[addr] = val == 1
            except (IndexError, ValueError):
                pass

    # Build upstream lookup by address
    upstream_map = {}
    for u in upstreams:
        upstream_map[u["address"]] = u

    services = []
    for route in routes:
        route_id = route.get("@id", "unknown")
        hosts = []
        for m in route.get("match", []):
            hosts.extend(m.get("host", []))
        sock = ""
        for h in route.get("handle", []):
            for u in h.get("upstreams", []):
                sock = u.get("dial", "")

        # Normalize socket path for lookups
        sock_file = sock.replace("unix/", "").replace("unix//", "/")
        sock_addr = sock  # Caddy uses "unix//path" in upstream addresses
        sock_exists = os.path.exists(sock_file)

        upstream_info = upstream_map.get(sock_addr, {})
        healthy = health_map.get(sock_addr)
        # If not in metrics, infer from socket existence
        if healthy is None:
            healthy = sock_exists

        services.append({
            "id": route_id,
            "hosts": hosts,
            "socket": sock_file,
            "socket_exists": sock_exists,
            "healthy": healthy,
            "requests": upstream_info.get("num_requests", 0),
            "fails": upstream_info.get("fails", 0),
        })

    # TLS info
    automate = tls_config.get("certificates", {}).get("automate", [])
    policies = tls_config.get("automation", {}).get("policies", [])
    tls_domains = automate or [s for p in policies for s in p.get("subjects", [])]

    # Dynamic DNS info
    dns_domains = dyn_dns.get("domains", {})
    dns_versions = dyn_dns.get("versions", {})

    return {
        "services": services,
        "tls_domains": tls_domains,
        "dns": {
            "domains": dns_domains,
            "ipv4": dns_versions.get("ipv4", False),
            "ipv6": dns_versions.get("ipv6", False),
        },
    }


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dev-mesh</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 0.85rem; margin-bottom: 24px; }
  .section-label { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #555; margin-bottom: 12px; }
  .service { background: #161616; border: 1px solid #222; border-radius: 8px; padding: 16px; margin-bottom: 10px; }
  .service-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .service-name { font-weight: 600; font-size: 0.95rem; }
  .service-health { display: flex; align-items: center; font-size: 0.8rem; color: #888; }
  .service-details { display: flex; flex-wrap: wrap; gap: 4px 16px; }
  .service-detail { font-size: 0.8rem; color: #666; }
  .service-detail a { color: #7cacf8; text-decoration: none; }
  .service-detail a:hover { text-decoration: underline; }
  .mono { font-family: 'SF Mono', 'Menlo', monospace; font-size: 0.75rem; color: #555; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; flex-shrink: 0; }
  .dot.green { background: #22c55e; box-shadow: 0 0 6px #22c55e44; }
  .dot.red { background: #ef4444; box-shadow: 0 0 6px #ef444444; }
  .dot.yellow { background: #eab308; box-shadow: 0 0 6px #eab30844; }
  .infra { background: #161616; border: 1px solid #222; border-radius: 8px; padding: 16px; margin-bottom: 10px; display: flex; gap: 24px; flex-wrap: wrap; }
  .meta-item { font-size: 0.85rem; }
  .meta-label { color: #555; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .empty { color: #444; font-style: italic; text-align: center; padding: 32px; background: #161616; border: 1px solid #222; border-radius: 8px; }
  .refresh { position: fixed; bottom: 16px; right: 16px; font-size: 0.7rem; color: #333; }
  @media (max-width: 600px) {
    body { padding: 16px; }
    .infra { gap: 12px; }
  }
</style>
</head>
<body>
<h1>dev-mesh</h1>
<p class="subtitle" id="subtitle">loading...</p>
<p class="section-label">Services</p>
<div id="services"><p class="empty">loading...</p></div>
<p class="section-label" style="margin-top:20px">Infrastructure</p>
<div class="infra" id="infra"></div>
<div class="refresh" id="refresh"></div>
<script>
async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    renderServices(d.services);
    renderInfra(d);
    document.getElementById('refresh').textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('refresh').textContent = 'error: ' + e.message;
  }
}

function renderServices(services) {
  const el = document.getElementById('services');
  if (!services.length) {
    el.innerHTML = '<p class="empty">No services registered</p>';
    document.getElementById('subtitle').textContent = 'No services running';
    return;
  }
  const healthy = services.filter(s => s.healthy).length;
  document.getElementById('subtitle').textContent = `${services.length} service${services.length === 1 ? '' : 's'} registered, ${healthy} healthy`;
  let html = '';
  for (const s of services) {
    const url = s.hosts[0] ? 'https://' + s.hosts[0] : '';
    const dotClass = s.healthy ? (s.socket_exists ? 'green' : 'yellow') : 'red';
    const status = s.healthy ? (s.socket_exists ? 'healthy' : 'no socket') : 'down';
    const fails = s.fails > 0 ? `<span style="color:#ef4444"> ${s.fails} failed</span>` : '';
    html += `<div class="service">
      <div class="service-header">
        <span class="service-name">${s.id}</span>
        <span class="service-health"><span class="dot ${dotClass}"></span>${status}</span>
      </div>
      <div class="service-details">
        <span class="service-detail"><a href="${url}">${s.hosts[0] || '-'}</a></span>
        <span class="service-detail mono">${s.socket.replace('/tmp/caddy-dev/', '')}</span>
        <span class="service-detail">${s.requests} req${s.requests===1?'':'s'}${fails}</span>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

function renderInfra(d) {
  const el = document.getElementById('infra');
  const domains = Object.entries(d.dns.domains || {}).map(([z,s]) => s.map(sub => `${sub}.${z}`).join(', ')).join(', ');
  const tls = (d.tls_domains || []).join(', ');
  const ip = d.dns.ipv4 ? 'IPv4' : '';
  el.innerHTML = `
    <div class="meta-item"><div class="meta-label">TLS</div>${tls || 'none'}</div>
    <div class="meta-item"><div class="meta-label">Dynamic DNS</div>${domains || 'none'}</div>
    <div class="meta-item"><div class="meta-label">IP Version</div>${ip || 'none'}</div>
  `;
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            data = json.dumps(get_status()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            data = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # silence request logging


class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        if os.path.exists(self.server_address):
            os.unlink(self.server_address)
        self.socket.bind(self.server_address)
        os.chmod(self.server_address, 0o777)
        self.server_address = self.server_address


def main():
    domain = discover_domain()
    if not domain:
        print("error: could not discover domain from Caddy config", file=sys.stderr)
        sys.exit(1)
    print(f"domain: {domain}")

    # Start HTTP server on Unix socket
    server = UnixHTTPServer(SOCK_PATH, DashboardHandler)

    def shutdown(sig, frame):
        print("\nshutting down...")
        deregister_from_caddy()
        server.shutdown()
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    register_with_caddy(domain)
    print(f"dashboard: https://{ROUTE_ID}.{domain}")
    server.serve_forever()


if __name__ == "__main__":
    main()
