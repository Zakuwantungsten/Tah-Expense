# Tahmeed API backend

Python 3.11+ FastAPI service over the existing MongoDB collections. It does not
import the desktop package, so the API can be deployed independently.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
python -m app.cli.migrate --check
python -m app.cli.migrate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The migration first prints duplicate conflicts and refuses to create any index
until all conflicts are resolved. Re-running it is safe.

## Authentication

Login returns a short-lived JWT access token and opaque refresh token. Only a
SHA-256 digest of the refresh secret is stored in `auth_sessions`. Refresh is
single-use and atomically rotates the secret; reuse revokes the session.
Logout revokes the current session, while password changes revoke all other
sessions. Access checks both the active user and revocation state on every
request.

Roles are `admin`, `accountant`, and `cashier`. Reads require authentication.
Management writes require admin or accountant. Accountants can manage cashier
and accountant accounts, while only administrators can create or modify an
administrator. All API routes are below `/v1`; health routes are `/health/live`
and `/health/ready`.

## Backups

Install MongoDB Database Tools and configure S3-compatible credentials through
environment variables. Then schedule:

```powershell
python -m app.cli.backup preflight
python -m app.cli.backup run --cadence daily
python -m app.cli.backup retry
python -m app.cli.backup prune
```

`run` creates a local gzip archive and external JSON manifest without depending
on S3. The default standalone mode is an off-hours best-effort logical dump,
not PITR. Replica sets can explicitly select oplog-consistent mode; see the
deployment guide for its whole-instance dump and permission implications.
`retry` verifies uploaded archive and manifest metadata with `HeadObject`.
Uploaded local files retain three generations per cadence by default, while
`prune` applies remote 7 daily/4 weekly/6 monthly retention. Commands share a
local `flock` and Mongo lease so only one backup host is active. Mongo
credentials are passed to mongodump through a temporary mode-0600 config file.
`all` remains available for manual use.

Production Ubuntu units, private Tailscale HTTPS guidance, rollout steps, and
the restore/disaster-recovery runbook are in `../deployment/v1/`.
