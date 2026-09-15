#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "=== Killing old processes ==="
fuser -k 8000/tcp 2>/dev/null || true
fuser -k 3000/tcp 2>/dev/null || true
sleep 2

mkdir -p uploads outputs logs db pdf_reports

echo "=== Starting Backend (port 8000) ==="
cd "$APP_DIR/backend"
source ~/.bashrc 2>/dev/null || true
nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$APP_DIR/logs/backend.log" 2>&1 &
echo "  PID: $!"

echo "=== Starting Frontend (port 3000) ==="
cd "$APP_DIR/frontend-next"
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 20 2>/dev/null || true

# `npm run start` (next start) serves a production build — it needs `.next`
# to already exist, which `npm run build` (next build) creates. That build
# step was missing here, so this would fail with "Could not find a
# production build" on any fresh checkout (only worked before because a
# `.next` happened to already be on disk from a prior manual build).
if [ ! -d ".next" ]; then
  echo "  No production build found — running 'npm run build' (first run only)..."
  npm run build
fi

nohup bash -c "export NVM_DIR=\"\$HOME/.nvm\"; [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"; nvm use 20; npm run start" > "$APP_DIR/logs/frontend.log" 2>&1 &
echo "  PID: $!"

sleep 3
echo ""
echo "=== Done ==="
echo "  Frontend : http://192.168.3.32:3000"
echo "  API      : http://192.168.3.32:8000/docs"
echo "  Logs     : $APP_DIR/logs/"
echo "  Login    : admin / changeme123"
