# start.ps1 — Launch Scenario Debugger (Windows dev)
#
# USAGE:
#   Right-click → "Run with PowerShell"
#   OR from a terminal: .\start.ps1
#
# Opens two separate windows:
#   1. FastAPI backend  → http://127.0.0.1:8000
#   2. Next.js frontend → http://localhost:3000

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  OFSAA Scenario Debugger — Starting" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── Backend ──────────────────────────────────────────────────────────
Write-Host "Starting FastAPI backend on http://127.0.0.1:8000 ..." -ForegroundColor Yellow

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
) -WindowStyle Normal

# Give the backend a moment to start before the frontend tries to connect
Start-Sleep -Seconds 3

# ── Frontend ─────────────────────────────────────────────────────────
Write-Host "Starting Next.js frontend on http://localhost:3000 ..." -ForegroundColor Yellow

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\frontend-next'; npm run dev"
) -WindowStyle Normal

Write-Host ""
Write-Host "Both processes started." -ForegroundColor Green
Write-Host "Open http://localhost:3000 in your browser." -ForegroundColor Green
Write-Host ""
Write-Host "Default login: admin / changeme123" -ForegroundColor Magenta
Write-Host "(Change this password immediately after first login)" -ForegroundColor Magenta
Write-Host ""
