# Build the sole release artifact: the per-user Inno Setup installer.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$BuildEnv = Join-Path $Root ".env.build"
# Remove residue even when a later prerequisite validation fails.
Remove-Item -Force $BuildEnv -ErrorAction SilentlyContinue

function Find-Iscc {
    $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Command) { return $Command.Source }
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($Candidates.Count -gt 0) { return @($Candidates)[0] }
    throw "ISCC.exe not found. Install Inno Setup 6 or add ISCC.exe to PATH."
}

if (-not (Test-Path ".env.production")) {
    throw "Copy .env.production.example to .env.production and fill every placeholder."
}

$VersionSource = Get-Content "tahmeed\version.py" -Raw
if ($VersionSource -notmatch '(?m)^APP_VERSION\s*=\s*"(?<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))"\s*$') {
    throw "tahmeed\version.py must contain one stable SemVer APP_VERSION."
}
$Version = $Matches.version
$ProductionEnv = Get-Content ".env.production" -Raw
if ($ProductionEnv -notmatch '(?m)^UPDATE_MANIFEST_URL=https://[^/\s]+/.+$') {
    throw "UPDATE_MANIFEST_URL must be an HTTPS R2 custom-domain URL."
}
if ($ProductionEnv -match 'YOUR-|REPLACE_|yourdomain\.com|example\.com|HOSTNAME\.TAILNET') {
    throw ".env.production still contains a placeholder."
}

try {
    Remove-Item -Force $BuildEnv -ErrorAction SilentlyContinue
    Copy-Item -Force ".env.production" $BuildEnv

    Write-Host "Installing pinned application and build dependencies..."
    python -m pip install -q -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    Write-Host "Building desktop version $Version..."
    python -m PyInstaller --noconfirm --clean tahmeed.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    $Iscc = Find-Iscc
    Write-Host "Compiling per-user installer with $Iscc..."
    & $Iscc "/DMyAppVersion=$Version" "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

    $Installer = Join-Path $Root "installer_output\TahmeedExpenseSetup-$Version.exe"
    if (-not (Test-Path $Installer)) {
        throw "Expected installer was not produced: $Installer"
    }
    Write-Host "Built sole release artifact:"
    Write-Host "  $Installer"
}
finally {
    Remove-Item -Force $BuildEnv -ErrorAction SilentlyContinue
}
