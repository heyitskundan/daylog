# Build the daylog Windows installer end to end.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Steps: sync deps -> render icon -> PyInstaller (dist\daylog\) -> Inno Setup
# (packaging\Output\daylog-setup.exe). Run from the project root.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Syncing dependencies (tray + build)..." -ForegroundColor Cyan
uv sync --extra tray --group build

Write-Host "==> Rendering icon..." -ForegroundColor Cyan
uv run python packaging\make_icon.py

Write-Host "==> Building the executable with PyInstaller..." -ForegroundColor Cyan
uv run pyinstaller packaging\daylog.spec --noconfirm --clean

# Locate the per-user Inno Setup compiler.
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $iscc)) {
    throw "ISCC.exe not found. Install Inno Setup 6 (winget install JRSoftware.InnoSetup)."
}

Write-Host "==> Compiling the installer with Inno Setup..." -ForegroundColor Cyan
& $iscc packaging\daylog.iss

$out = Join-Path $root "dist\daylog-setup.exe"
Write-Host ""
Write-Host "Done. Installer: $out" -ForegroundColor Green
