# Local development (safe loop)

Use this for every feature/fix. **Do not** point desktop or backend at the production Mongo host (Tailscale `100.x` / server) while coding.

```
Desktop (tahmeed/)  →  http://127.0.0.1:8000  →  local API
                                              →  MongoDB on 127.0.0.1:27017
                                                 DB: tahmeed_expense_dev
```

The desktop app does **not** open Mongo itself — only the API does. Production installs use `https://api.meedexpe.com` with no desktop `MONGODB_URI`.

## Prerequisites

- MongoDB Community running locally (Windows service `MongoDB` is fine)
- Python 3.11+ with project deps installed

## One-time env setup

Root `.env` (desktop) and `backend/.env` (API) are gitignored. Copy from the examples:

```powershell
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
```

Desktop needs the API URL:

```env
API_BASE_URL=http://127.0.0.1:8000
```

Backend must use loopback Mongo and a **dev** database name (`backend/.env`):

```env
MONGODB_URI=mongodb://127.0.0.1:27017/?directConnection=true
DB_NAME=tahmeed_expense_dev
JWT_SECRET=…  # ≥32 chars — see backend/.env.example
# Optional local receipt disk (absolute path). Leave unset to use ./var/attachments.
# Or set it in the desktop: Admin / Accountant → Receipt disk.
ATTACHMENTS_DIRECTORY=C:\Users\Tungsten\Desktop\tahmeed-expense\var\attachments
```

### Local receipt-disk check

1. Start the API (`.\scripts\dev_api.ps1`) and desktop against `http://127.0.0.1:8000`.
2. Sign in as admin or accountant → **Receipt disk**.
3. Confirm the host is your PC, pick a drive (or type an absolute folder), **Test write**, then **Apply path**.
4. Attach a file on a register row, restart the API, open the attachment again — it must still download from that folder.

### Local Expo receipt app

1. API running and reachable from the phone (LAN IP, not `127.0.0.1`).
2. `cd mobile` → copy `.env.example` to `.env` → set `EXPO_PUBLIC_API_BASE_URL=http://YOUR_LAN_IP:8000`.
3. `npm start` and open in Expo Go.
4. Sign in as a user linked to an ownership person that has an open `needed` receipt request.

Dev startup refuses Tailscale CGNAT (`100.64.0.0/10`) and `*.ts.net` Mongo/API hosts unless you explicitly set `TAHMEED_ALLOW_REMOTE_DB=1` (emergency only).

## Daily workflow

1. Confirm Mongo is up (service running / port 27017).
2. Start the API:

```powershell
.\scripts\dev_api.ps1
```

3. In another terminal, run the desktop app as usual from the repo (loads root `.env`).
4. First empty DB: create the bootstrap admin via the app’s first-run screen (hits `POST /v1/auth/bootstrap`).
5. Exercise the change against local data.
6. Only then commit / deploy to the server.

Health check:

```powershell
curl http://127.0.0.1:8000/health/ready
```

## What must never be in local `.env`

- Tailscale IPs (`100.x.x.x`)
- `*.ts.net` API URLs
- Production passwords for `tahmeed_app`
- Pointing the desktop at production Mongo (desktop is API-only; keep `MONGODB_URI` out of root `.env` unless running admin scripts)

`.env.production` is for **installer builds only**, not day-to-day coding. It must include `API_BASE_URL` only (no Mongo URI).
