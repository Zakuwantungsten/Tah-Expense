# Security implementation

This document describes the application-level security controls for Tahmeed Expense
when the API is reached over the public internet (Cloudflare Tunnel → loopback
FastAPI). Host firewall (UFW), Mongo bind address, and Cloudflare WAF remain
server/ops concerns and are not managed from the desktop app.

## Threat model

| Surface | Exposure | Primary controls |
|---------|----------|------------------|
| FastAPI `/v1/*` | Public via `api.meedexpe.com` | JWT auth, IP allowlist, MAC allowlist, login lockout, password policy |
| MongoDB | Must stay on `127.0.0.1:27017` | Host firewall; never open Funnel |
| `/docs` | Optional | Disabled by default (`security_docs_enabled`) |
| Desktop clients | Trusted office PCs | Registered IPs + MACs |

## Client IP resolution

Uvicorn behind `cloudflared` sees the connector as `127.0.0.1`. The API resolves
the real client as:

1. `CF-Connecting-IP` (preferred)
2. First hop in `X-Forwarded-For` when the immediate peer is loopback
3. Otherwise `request.client.host`

## MAC allowlist (soft control)

A real NIC MAC does **not** travel over Cloudflare HTTPS. The Windows desktop
client sends its primary adapter MAC as `X-Device-Mac` on every request. The
server normalizes and compares against `security_allowed_macs`.

This is a **soft** control: headers can be spoofed. It is useful together with
IP allowlisting to limit known office machines, not as a cryptographic
device attestation.

## Settings keys (`system_settings`)

| Key | Default | Purpose |
|-----|---------|---------|
| `security_ip_allowlist_enabled` | `false` | Enforce IP allowlist |
| `security_allowed_ips` | `[]` | Exact IPs or CIDR |
| `security_mac_allowlist_enabled` | `false` | Enforce MAC allowlist |
| `security_allowed_macs` | `[]` | `AA:BB:CC:DD:EE:FF` |
| `security_password_min_length` | `10` | Minimum password length |
| `security_password_require_upper` | `true` | Require A–Z |
| `security_password_require_lower` | `true` | Require a–z |
| `security_password_require_digit` | `true` | Require 0–9 |
| `security_password_require_special` | `false` | Require special character |
| `security_login_max_attempts` | `5` | Failures before lock |
| `security_login_lockout_minutes` | `15` | Lock duration |
| `security_docs_enabled` | `false` | Allow `/docs` and OpenAPI |
| `backup_schedule_mode` | `hourly` | `hourly` \| `daily` \| `disabled` |
| `backup_daily_time_utc` | `00:00` | Daily dump time when mode=`daily` |
| `backup_keep_count` | `30` | Keep newest N remote backups |

Allowlist toggles default **off**. Enabling with an empty list denies all
clients — register office IPs/MACs first, then enable.

Writes to `security_*` and `backup_*` settings require the **admin** role.

## Login lockout

Failed logins are tracked in `auth_lockouts` keyed by normalized username +
client IP. After `security_login_max_attempts` failures, further attempts return
`429 account_locked` until the lockout window expires. A successful login clears
the counter for that key.

## Emergency bypass

Set `TAHMEED_SECURITY_BYPASS=1` on the API host to disable IP/MAC allowlists and
login lockout (ops escape hatch if you lock yourself out). Do not leave this
enabled permanently. Password policy still applies.

`TAHMEED_DOCS_ENABLED=1` forces OpenAPI/docs routes on at process start
(in addition to the admin toggle for runtime gating).

## Health and bootstrap

- `/health/live` and `/health/ready` skip IP/MAC allowlists.
- `/v1/auth/bootstrap` and `/v1/auth/bootstrap-status` skip MAC allowlist so the
  first admin can enroll; IP allowlist still applies when enabled.

## Rollout checklist

1. Deploy API with allowlists **disabled**.
2. Sign in as admin → **Security** tab → note “This PC’s MAC” and public IP.
3. Add office IPs (and CIDRs) and all office PC MACs.
4. Enable IP allowlist, confirm login still works.
5. Enable MAC allowlist, confirm login still works from each office PC.
6. Tune password policy and lockout; leave docs disabled in production.
7. Configure backup mode (`hourly` / `daily` / `disabled`) and keep count (30).
8. Confirm accountant users no longer see Backups; only admin can list/restore.

## Login sessions (Phase 1)

Admin → **Security** shows active sessions (IP, device MAC, user-agent).

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/sessions` | List sessions (admin) |
| `POST /v1/sessions/{id}/revoke` | Force-logout one session |
| `POST /v1/sessions/revoke-user/{user_id}` | Force-logout all for a user |
| `POST /v1/sessions/revoke-all` | Force-logout everyone except caller |

Settings: `security_max_sessions_per_user` (0 = unlimited), `security_idle_session_days`
(0 = off). New logins store `device_mac` from `X-Device-Mac`.

### User admin (Phase 2)

Admin → **Users** → Actions: Edit, Reset password, Ban/Unban, Activate/Deactivate,
Force logout, Delete (soft). Ban requires a reason; unban leaves the account
inactive until Activate. Soft-deleted users are hidden from the default list.

See also [`SESSIONS_USERS_AUDIT_GAPS.md`](SESSIONS_USERS_AUDIT_GAPS.md) (Phases 1–3 complete).

## Audit Log

Admin → **Audit Log** reads `GET /v1/audit/events` (admin only). The API
records at least:

- `auth.login` (ok / fail), `auth.logout`, `auth.bootstrap`, `auth.change_password`
- `auth.refresh_reuse` on stolen refresh reuse
- `user.create` / `user.update` / ban / soft-delete / session revoke
- `settings.update` (including security and backup keys)
- `txn.*`, `recon.*`, catalog (`category` / `rule` / `mapping` / `subtable`), `fleet.*`, `people.*`
- `diesel.items_update`, `backup.restore` (plus `restore_audit` collection)
- Existing business actions already written by accountant/import/feed modules

## Audit Log UI (Phase 3.4)

- Preset chips: All / Auth / Users / Transactions / Settings
- Export CSV of the current filtered result set
- Users → Actions → “Sessions for this user…” / “Audit for this user…”

## Still out of scope

- Managing host UFW from the desktop app
- Automating Cloudflare Access / WAF rules
- Production rollout / enabling max-sessions policy in the office
