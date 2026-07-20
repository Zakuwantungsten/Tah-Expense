# Hash and sign the existing Inno installer, then optionally publish to R2.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(1, [long]::MaxValue)]
    [long]$Sequence,
    [Parameter(Mandatory = $true)][string]$KeyId,
    [Parameter(Mandatory = $true)][string]$PrivateKeyPath,
    [string]$MinimumSupportedVersion,
    [string]$NotesFile = "releases\notes.md",
    [switch]$Upload,
    [string]$R2Bucket,
    [string]$R2EndpointUrl
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$VersionSource = Get-Content "tahmeed\version.py" -Raw
if ($VersionSource -notmatch '(?m)^APP_VERSION\s*=\s*"(?<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))"\s*$') {
    throw "Invalid authoritative version in tahmeed\version.py."
}
$Version = $Matches.version
if (-not $MinimumSupportedVersion) { $MinimumSupportedVersion = $Version }
$Installer = Join-Path $Root "installer_output\TahmeedExpenseSetup-$Version.exe"
if (-not (Test-Path $Installer)) {
    throw "Expected installer not found. Run .\scripts\build_windows.ps1 first: $Installer"
}
if (-not (Test-Path $PrivateKeyPath)) { throw "Private key not found." }
if (-not (Test-Path $NotesFile)) { throw "Release notes file not found: $NotesFile" }
if (-not (Test-Path ".env.production")) { throw ".env.production not found." }
$ProductionEnv = Get-Content ".env.production" -Raw
if ($ProductionEnv -notmatch '(?m)^UPDATE_MANIFEST_URL=(?<url>https://\S+)\s*$') {
    throw "A valid HTTPS UPDATE_MANIFEST_URL is required."
}
$ManifestUrl = $Matches.url
$ManifestUri = [Uri]$ManifestUrl
$ObjectPrefix = $ManifestUri.AbsolutePath.TrimStart("/")
$ObjectPrefix = Split-Path $ObjectPrefix -Parent
if ($ObjectPrefix -eq ".") { $ObjectPrefix = "" }
$ObjectPrefix = $ObjectPrefix.Replace("\", "/")
$ReleaseDir = Join-Path $Root "releases"

python "scripts\create_signed_release.py" `
    --installer $Installer `
    --version $Version `
    --sequence $Sequence `
    --minimum-supported-version $MinimumSupportedVersion `
    --notes-file $NotesFile `
    --manifest-url $ManifestUrl `
    --key-id $KeyId `
    --private-key $PrivateKeyPath `
    --output-dir $ReleaseDir
if ($LASTEXITCODE -ne 0) { throw "Release signing failed." }

if ($Upload) {
    if (-not $R2Bucket -or -not $R2EndpointUrl) {
        throw "-Upload requires -R2Bucket and -R2EndpointUrl."
    }
    if ($R2EndpointUrl -notmatch '^https://') { throw "R2 endpoint must be HTTPS." }
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        throw "AWS CLI is required for R2 upload."
    }
    $Base = "s3://$R2Bucket"
    if ($ObjectPrefix) { $Base = "$Base/$ObjectPrefix" }
    # Ordering is a safety property: clients can never see a manifest before
    # both objects it references are available.
    aws s3 cp $Installer "$Base/$([IO.Path]::GetFileName($Installer))" --endpoint-url $R2EndpointUrl
    if ($LASTEXITCODE -ne 0) { throw "R2 installer upload failed." }
    aws s3 cp "$ReleaseDir\version.json.sig" "$Base/version.json.sig" --endpoint-url $R2EndpointUrl
    if ($LASTEXITCODE -ne 0) { throw "R2 signature upload failed." }
    aws s3 cp "$ReleaseDir\version.json" "$Base/version.json" --endpoint-url $R2EndpointUrl
    if ($LASTEXITCODE -ne 0) { throw "R2 manifest upload failed." }
    Write-Host "Published installer, signature, then manifest to R2."
}
else {
    Write-Host "Signed locally. Re-run with -Upload after review."
}
