# Package a release zip for users to download.
# Run after build_windows.ps1 and after bumping APP_VERSION in tahmeed/config.py.
#
# Usage:
#   .\scripts\publish_release.ps1 -Version 1.0.1

param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Dist = Join-Path $Root "dist\Tahmeed Expense"
if (-not (Test-Path $Dist)) {
    Write-Error "Build folder not found. Run .\scripts\build_windows.ps1 first."
}

$ReleaseDir = Join-Path $Root "releases"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$ZipName = "TahmeedExpense-$Version.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $Dist -DestinationPath $ZipPath

$Manifest = @{
    version       = $Version
    download_url  = "https://updates.yourdomain.com/tahmeed/$ZipName"
    release_notes = "Update $Version"
} | ConvertTo-Json -Depth 3

$ManifestPath = Join-Path $ReleaseDir "version.json"
$Manifest | Set-Content -Encoding UTF8 $ManifestPath

Write-Host ""
Write-Host "Created:"
Write-Host "  $ZipPath"
Write-Host "  $ManifestPath"
Write-Host ""
Write-Host "Next: upload both files to your update server (see releases/README below)."
