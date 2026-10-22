# Start the local Tahmeed API against loopback Mongo (see LOCAL_DEV.md).
# --reload is the default. Saving a Python file then logs "Shutting down"
# and a new "Application startup complete", and drops any request in flight.
# Pass -NoReload while testing import or park so a save cannot kill the batch.
# Pass -Office for the shared PC: 2 workers, reload off. Each worker has its own
# Mongo client (maxPoolSize 20).
param(
    [switch]$NoReload,
    [switch]$Office
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Backend = Join-Path $Root "backend"
Set-Location $Backend

if (-not (Test-Path ".env")) {
    throw "Missing backend\.env - copy backend\.env.example and use 127.0.0.1 + tahmeed_expense_dev."
}

$EnvText = Get-Content ".env" -Raw
if ($EnvText -match '100\.(6[4-9]|[7-9]\d|1[01]\d)\.|tail[0-9a-z]*\.ts\.net') {
    throw "backend\.env still points at Tailscale/production. Fix it before starting the API (see LOCAL_DEV.md)."
}
if ($EnvText -notmatch '(?m)^MONGODB_URI=mongodb://127\.0\.0\.1') {
    throw "backend\.env MONGODB_URI must use mongodb://127.0.0.1 for local development."
}

$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing backend\.venv - create it with: python -m venv .venv && .\.venv\Scripts\pip install -e `".[dev]`""
}

Write-Host "Starting API on http://127.0.0.1:8000 (DB from backend\.env)..."
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
)
if ($Office) {
    Write-Host "Office mode: 2 workers, reload off."
    $uvicornArgs += "--workers", "2"
} elseif ($NoReload) {
    Write-Host "Reload is off. Saving a file will not restart this API."
} else {
    $uvicornArgs += "--reload"
}

& $Python @uvicornArgs
