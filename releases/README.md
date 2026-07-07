# Hosting app updates

Upload these to a static URL reachable by all client PCs (HTTPS).

## Files

- `version.json` — checked on every app launch
- `TahmeedExpense-X.Y.Z.zip` — full app folder users download

## version.json format

```json
{
  "version": "1.0.1",
  "download_url": "https://updates.yourdomain.com/tahmeed/TahmeedExpense-1.0.1.zip",
  "release_notes": "- Fixed fuel report\n- Added export"
}
```

## Publish workflow

1. Bump `APP_VERSION` in `tahmeed/config.py` (e.g. `1.0.0` → `1.0.1`).
2. Build: `.\scripts\build_windows.ps1`
3. Package: `.\scripts\publish_release.ps1 -Version 1.0.1`
4. Upload `releases/version.json` and `releases/TahmeedExpense-1.0.1.zip` to your server.
5. Users see "Update Available" on next launch.

## Hosting on Ubuntu (with cloudflared)

Serve static files with nginx or Python:

```bash
mkdir -p ~/tahmeed-updates
# copy version.json and zip here
cd ~/tahmeed-updates
python3 -m http.server 8080
```

Point cloudflared at `http://localhost:8080` for e.g. `updates.yourdomain.com`.

Set `UPDATE_MANIFEST_URL` in `.env.production` before building:

```env
UPDATE_MANIFEST_URL=https://updates.yourdomain.com/version.json
```

## User update steps

1. App shows dialog → user clicks **Download Update**
2. User extracts zip over the old install folder (or IT does it)
3. User reopens Tahmeed Expense
