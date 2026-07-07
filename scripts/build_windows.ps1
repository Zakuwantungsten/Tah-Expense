# Build a distributable Windows app folder.
# Prerequisites: Python 3.10+, venv activated, .env.production filled in.
#
# Usage (from project root):
#   .\scripts\build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path ".env.production")) {
    Write-Error @"
.env.production not found.

Copy .env.production.example to .env.production and set your Ubuntu MongoDB URI:
  copy .env.production.example .env.production

"@
}

Write-Host "Installing build dependencies..."
pip install -q -r requirements.txt pyinstaller

Copy-Item -Force ".env.production" ".env.build"

Write-Host "Building Tahmeed Expense..."
pyinstaller --noconfirm tahmeed.spec

Remove-Item -Force ".env.build" -ErrorAction SilentlyContinue

$Out = Join-Path $Root "dist\Tahmeed Expense"
Write-Host ""
Write-Host "Done. Distributable folder:"
Write-Host "  $Out"
Write-Host ""
Write-Host "Zip that folder or copy it to each PC. Users run 'Tahmeed Expense.exe'."
Write-Host "They also need Tailscale installed and joined to your company tailnet."
