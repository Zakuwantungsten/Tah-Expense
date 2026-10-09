# Cloudflare Tunnel for `api.meedexpe.com`

Public HTTPS for the Tahmeed API. The Docker (or systemd) API stays on
`127.0.0.1:8000`. **cloudflared** runs on the host (systemd), same idea as the
old Tailscale Serve proxy — not a Compose sidecar.

```
Desktop / curl
      │  HTTPS
      ▼
https://api.meedexpe.com     ← Cloudflare edge
      │
      ▼
cloudflared (host systemd)   ← tunnel to this Ubuntu box
      │
      ▼
http://127.0.0.1:8000        ← Docker/uvicorn (unchanged)
      │
      ▼
MongoDB 127.0.0.1:27017      ← never public
```

Do **not** stop the API containers or Tailscale Serve to add the tunnel. Run
both proxies in parallel until `https://api.meedexpe.com/health/ready` is green,
then cut Serve.

## Prerequisites

- Cloudflare zone for `meedexpe.com` active (Namecheap nameservers → Cloudflare
  if not already, same pattern as your other projects).
- API already healthy on the host:

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
sudo ss -ltnp | grep ':8000'   # must show 127.0.0.1 only
```

- Ports `8000` and `27017` not open on the public internet (UFW deny is fine).

## 1. Create the tunnel (Cloudflare dashboard)

1. Zero Trust → Networks → Tunnels → Create a tunnel.
2. Name it e.g. `tahmeed-api` (Cloudflared connector).
3. Copy the install command / token Cloudflare shows for Linux.
4. Public hostname:
   - **Subdomain:** `api`
   - **Domain:** `meedexpe.com`
   - **Service:** `HTTP` → `http://127.0.0.1:8000`
5. Save. Cloudflare will create the DNS record for `api.meedexpe.com` (usually a
   CNAME to the tunnel).

Prefer the **token install** (no long-lived credentials file on disk beyond what
the package stores). Do not commit tunnel tokens to git.

## 2. Install cloudflared on Ubuntu

Use Cloudflare’s documented package install (or the one-liner from the dashboard
token step). Example with a token from the dashboard:

```bash
# Follow the current Cloudflare “Install connector” commands for Debian/Ubuntu,
# then enable the service that embeds your tunnel token, e.g.:
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared --no-pager
```

Confirm the connector shows **Healthy** in the Zero Trust Tunnels UI.

If you already use a `config.yml` style connector, the ingress rule must include:

```yaml
ingress:
  - hostname: api.meedexpe.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

## 3. Overlap checklist (keep Tailscale Serve)

While validating the public URL, leave Serve running so existing Tailscale pilots
keep working:

```bash
tailscale serve status
curl --fail http://127.0.0.1:8000/health/ready
```

From a machine **without** Tailscale (phone hotspot / another network):

```bash
curl -fsS https://api.meedexpe.com/health/live
curl -fsS https://api.meedexpe.com/health/ready
```

Both must return success. If public HTTPS fails but loopback works, fix DNS or
the tunnel route — do not disable Serve yet.

## 4. Cut Tailscale Serve

Only after public health checks pass:

```bash
sudo tailscale serve reset
tailscale serve status
```

Keep the Tailscale **node** enrolled if you still want admin SSH or other
tailnet access. Never enable Tailscale Funnel for this API.

Optional: leave Serve off permanently; end-user desktops use
`https://api.meedexpe.com` only (see root `.env.production.example`).

## 5. Firewall reminder

```bash
sudo ufw status
sudo ss -ltnp | grep -E ':(8000|27017)\s'
```

- API: `127.0.0.1:8000` only.
- Mongo: `127.0.0.1:27017` only.
- Do not open public `8000` or `27017`. Cloudflare Tunnel outbound connections
  do not require inbound 443 on your host for the API path.

## 6. Optional: refresh API image

Tunnel works against the **already-running** container. Rebuild/pull only when
you want newer backend code:

```bash
cd /opt/tahmeed-expense/deployment/docker
# After syncing a reviewed release to /opt/tahmeed-expense:
sudo scripts/deploy.sh <new-tag>
curl --fail http://127.0.0.1:8000/health/ready
curl -fsS https://api.meedexpe.com/health/ready
```

## 7. Desktop installer

Bake only:

```dotenv
API_BASE_URL=https://api.meedexpe.com
UPDATE_MANIFEST_URL=https://updates.example.com/tahmeed/version.json
```

Do not include `MONGODB_URI` in production desktop builds (Phase 11).

## Rollback

If public routing breaks after cutting Serve:

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```

Fix the tunnel, re-verify `https://api.meedexpe.com/health/ready`, then
`sudo tailscale serve reset` again.
