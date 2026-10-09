# Start the local Tahmeed API against loopback Mongo (see LOCAL_DEV.md).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Backend = Join-Path $Root "backend"
Set-Location $Backend

if (-not (Test-Path ".env")) {
    throw "Missing backend\.env — copy backend\.env.example and use 127.0.0.1 + tahmeed_expense_dev."
}

$EnvText = Get-Content ".env" -Raw
if ($EnvText -match '100\.(6[4-9]|[7-9]\d|1[01]\d)\.|tail[0-9a-z]*\.ts\.net') {
    throw "backend\.env still points at Tailscale/production. Fix it before starting the API (see LOCAL_DEV.md)."
}
if ($EnvText -notmatch '(?m)^MONGODB_URI=mongodb://127\.0\.0\.1') {
    throw "backend\.env MONGODB_URI must use mongodb://127.0.0.1 for local development."
}

Write-Host "Starting API on http://127.0.0.1:8000 (DB from backend\.env)..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
