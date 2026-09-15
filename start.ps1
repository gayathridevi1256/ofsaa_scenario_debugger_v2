# start.ps1 - Launch Scenario Debugger (Windows dev), one command.
#
# USAGE:
#   Right-click -> "Run with PowerShell"
#   OR from a terminal: .\start.ps1
#
# Opens two separate windows:
#   1. FastAPI backend  -> http://127.0.0.1:8000
#   2. Next.js frontend -> http://localhost:3000
#
# Handles, automatically, quirks found running this project on Windows:
#   - Node.js is NOT required to be installed system-wide or with admin
#     rights -- if none is found on PATH, a portable copy is downloaded once
#     into tools\node\ (no installer, no elevation prompt).
#   - This project's folder path may contain characters (e.g. "&") that
#     break npm's generated .cmd shims (node_modules\.bin\next.cmd -- a
#     documented Windows npm limitation). Next.js is invoked directly via
#     node.exe instead of through "npm run dev" / next.cmd.
#   - node_modules may be missing the Windows-native Next.js SWC binary if
#     it was synced from a Linux machine (git does not track node_modules,
#     but OneDrive/zip copies do) -- installed automatically if missing.
#   - .env's APP_BASE_PATH may be a Linux-style path meant for the real
#     deployment server -- detected and overridden with a local Windows path
#     for THIS session only; .env itself is never modified.

$ErrorActionPreference = "Stop"
$root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir  = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend-next"
$toolsDir    = Join-Path $root "tools\node"
$nodeVersion = "22.14.0"
$nextVersion = "16.2.11"   # must match frontend-next/node_modules/next's version

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  OFSAA Scenario Debugger - Starting" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# --- Locate or install Node.js (no admin rights required) ---------------
function Find-Or-Install-NodeDir {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { return (Split-Path $cmd.Source -Parent) }

    $localNode = Join-Path $toolsDir "node.exe"
    if (Test-Path $localNode) { return $toolsDir }

    Write-Host "Node.js not found - downloading a portable copy (one-time, no admin needed)..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $zipPath = Join-Path $toolsDir "node.zip"
    Invoke-WebRequest -Uri "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip" -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
    Remove-Item $zipPath
    $extracted = Join-Path $toolsDir "node-v$nodeVersion-win-x64"
    Get-ChildItem $extracted | Move-Item -Destination $toolsDir -Force
    Remove-Item $extracted -Recurse -Force
    Write-Host "Node.js installed to $toolsDir" -ForegroundColor Green
    return $toolsDir
}

$nodeDir = Find-Or-Install-NodeDir
$nodeExe = Join-Path $nodeDir "node.exe"
$npmCmd  = Join-Path $nodeDir "npm.cmd"
Write-Host "Using Node.js at: $nodeDir" -ForegroundColor DarkGray

# --- Ensure the Windows-native Next.js SWC binary is present ------------
$swcWin = Join-Path $frontendDir "node_modules\@next\swc-win32-x64-msvc"
if (-not (Test-Path $swcWin)) {
    Write-Host "Installing Windows build tools for Next.js (one-time)..." -ForegroundColor Yellow
    Push-Location $frontendDir
    & $npmCmd install "@next/swc-win32-x64-msvc@$nextVersion" --no-save --no-audit --no-fund
    Pop-Location
}

# --- Check .env for a Linux-style APP_BASE_PATH -------------------------
$envFile = Join-Path $root ".env"
$appBaseOverride = $null
if (Test-Path $envFile) {
    $match = Select-String -Path $envFile -Pattern '^APP_BASE_PATH=/' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($match) {
        $appBaseOverride = Join-Path $env:LOCALAPPDATA "ofsaa-scenario-debugger"
        Write-Host "NOTE: .env's APP_BASE_PATH looks Linux-style ($($match.Line)) - using '$appBaseOverride' for this Windows session instead. .env is left untouched." -ForegroundColor Yellow
    }
}

# --- Backend --------------------------------------------------------------
Write-Host "Starting FastAPI backend on http://127.0.0.1:8000 ..." -ForegroundColor Yellow
$backendEnvSet = ""
if ($appBaseOverride) { $backendEnvSet = "`$env:APP_BASE_PATH = '$appBaseOverride'; " }

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$backendDir'; ${backendEnvSet}uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
) -WindowStyle Normal

# Give the backend a moment to start before the frontend tries to connect
Start-Sleep -Seconds 3

# --- Frontend ---------------------------------------------------------
Write-Host "Starting Next.js frontend on http://localhost:3000 ..." -ForegroundColor Yellow
# Invoke Next.js directly via node.exe rather than "npm run dev" --
# node_modules\.bin\next.cmd lives inside THIS project's folder, and npm's
# generated .cmd shims break when that path contains characters like "&".
$nextBin = Join-Path $frontendDir "node_modules\next\dist\bin\next"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$frontendDir'; `$env:PATH = '$nodeDir;' + `$env:PATH; `$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8000'; & '$nodeExe' '$nextBin' dev"
) -WindowStyle Normal

Write-Host ""
Write-Host "Both processes started." -ForegroundColor Green
Write-Host "Open http://localhost:3000 in your browser." -ForegroundColor Green
Write-Host ""
Write-Host "Default login: admin / changeme123" -ForegroundColor Magenta
Write-Host "(Change this password immediately after first login)" -ForegroundColor Magenta
Write-Host ""
