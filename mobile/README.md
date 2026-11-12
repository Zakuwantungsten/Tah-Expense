# Tahmeed Receipts (Expo Go)

Mobile client for ownership people to clear open receipt requests, park early uploads, and review local upload history.

## Run locally

1. Start the API on your machine (`.\scripts\dev_api.ps1` from the repo root).
2. Copy `.env.example` to `.env` and set your LAN/hotspot API URL (phones **cannot** use `127.0.0.1`):

```env
EXPO_PUBLIC_API_BASE_URL=http://YOUR_LAN_IP:8000
```

3. From this folder:

```bat
npm start
```

4. Open the project in **Expo Go** on a phone on the **same network** as the API.

Default production URL (when `.env` is unset): `https://api.meedexpe.com`.

## LAN / hotspot (stable setup)

Phones must reach the API host IP on the shared subnet. Common traps:

| Setup | API bind / URL on phone |
| --- | --- |
| Same Wi‑Fi LAN | PC’s Wi‑Fi IPv4, e.g. `http://192.168.1.20:8000` |
| PC Wi‑Fi hotspot (phone joins hotspot) | Usually the hotspot gateway on the PC, often `http://192.168.137.1:8000` |
| USB / Ethernet + phone on Wi‑Fi | Different subnets → phone cannot reach PC unless you bridge or use hotspot |

Checklist when Expo shows “cannot reach” / spinning login:

1. On the PC, confirm the API listens on `0.0.0.0:8000` (not only `127.0.0.1`).
2. From the phone browser, open `http://YOUR_IP:8000/v1/health` (or docs) before blaming Expo.
3. Windows Firewall: allow inbound TCP 8000 for the Python/API process (Private networks).
4. Rebuild Expo config after changing `.env` — stop Metro, `npm start`, reload the app so `EXPO_PUBLIC_API_BASE_URL` updates.
5. Login screen footer shows the API base URL the build is using — verify it matches the working browser URL.

Hotspot tip: after enabling the PC hotspot, re-check `ipconfig` for the `192.168.137.x` adapter; put that gateway in `.env`.

## What it does

- Login / refresh against `/v1/mobile/auth/...`
- Home KPIs: open / overdue (≥48h) / reminded
- Open request detail: camera, photos, files; **atomic multi-file** upload with sent/failed summary
- **Park early** → desk parked inbox (no request yet)
- **Upload history** on-device (not a server ledger)
- Permission prompts deep-link to system Settings when blocked
- Expo push token after login; notification opens the request

Link a people directory row to the login user (`user_id`) or match `full_name` / ownership name so the list is not empty.
