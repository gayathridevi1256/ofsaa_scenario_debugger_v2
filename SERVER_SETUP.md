# OFSAA Scenario Debugger — Server Setup Guide

Server: `192.168.3.32` | User: `oracle` | Path: `/u01/AI/ofsaa_sql_debugger`

## Quick Start (after clone)

```bash
cd /u01/AI/ofsaa_sql_debugger

# 1. Setup .env
cp .env.example .env
# Edit .env — change these two lines:
#   APP_BASE_PATH=/u01/AI/ofsaa_sql_debugger
#   OUTPUT_BASE_PATH=/u01/AI/ofsaa_sql_debugger/outputs

# 2. Create runtime folders
mkdir -p uploads outputs logs db pdf_reports

# 3. Backend setup
cd backend
uv sync
cd ..

# 4. Frontend setup
cd frontend-next
source ~/.nvm/nvm.sh && nvm use 20
npm install --legacy-peer-deps
npm run build
cd ..

# 5. Run
./start.sh

# 6. Open: http://192.168.3.32:3000
# Login: admin / changeme123
```

---

## Prerequisites (one-time)

```bash
# Python 3.13 + uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# Node.js 20 via nvm (no root needed)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 20
nvm use 20

# Oracle Instant Client
# (Ask admin to install: sudo dnf install -y oracle-instantclient-basic oracle-instantclient-devel)

# Firewall ports (ask admin)
# sudo firewall-cmd --add-port=8000/tcp --add-port=3000/tcp --permanent
# sudo firewall-cmd --reload
```

---

## `.env` — Only 2 lines to change

The `.env.example` has sensible defaults. You only need to change:

| Line | Change to |
|------|-----------|
| `APP_BASE_PATH` | `/u01/AI/ofsaa_sql_debugger` |
| `OUTPUT_BASE_PATH` | `/u01/AI/ofsaa_sql_debugger/outputs` |

Everything else stays as-is — Oracle DB, SSH, API host (`127.0.0.1`) are all correct.

---

## Start / Stop / Status

### Start

```bash
/u01/AI/ofsaa_sql_debugger/start.sh
```

Or manually:

```bash
cd /u01/AI/ofsaa_sql_debugger
mkdir -p logs

# Backend (port 8000)
nohup bash -c 'cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000' > logs/backend.log 2>&1 &

# Frontend (port 3000)
nohup bash -c 'cd frontend-next && source ~/.nvm/nvm.sh && nvm use 20 && npm run start' > logs/frontend.log 2>&1 &
```

### Status

```bash
ps aux | grep -E "uvicorn|next start"
ss -tlnp | grep -E "8000|3000"
curl -s http://localhost:8000/api/health
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

### Stop

```bash
pkill -f "uvicorn app.main:app"
pkill -f "next start"
```

### View logs

```bash
tail -f /u01/AI/ofsaa_sql_debugger/logs/backend.log
tail -f /u01/AI/ofsaa_sql_debugger/logs/frontend.log
```

---

## Update App (after git changes)

```bash
cd /u01/AI/ofsaa_sql_debugger
git pull origin main

# Backend
cd backend && uv sync && cd ..

# Frontend
cd frontend-next
source ~/.nvm/nvm.sh && nvm use 20
npm install --legacy-peer-deps
npm run build
cd ..

# Restart
pkill -f "uvicorn app.main:app"
pkill -f "next start"
./start.sh
```

---

## Access

| URL | What |
|-----|------|
| `http://192.168.3.32:3000` | Frontend UI |
| `http://192.168.3.32:8000` | Backend API |
| `http://192.168.3.32:8000/docs` | Swagger API docs |
| `http://192.168.3.32:8000/api/health` | Health check |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `npm: command not found` | `source ~/.nvm/nvm.sh && nvm use 20` |
| `uv: command not found` | `source ~/.bashrc` |
| Build fails with Suspense error | `git pull origin main` (fix already committed) |
| `ORA-00904` / connection errors | Check Oracle DB is reachable from server |
| Port not open | Ask admin: `firewall-cmd --add-port=3000/tcp --add-port=8000/tcp` |
| Frontend shows blank page | Check `CORS_ORIGINS` in `.env` includes `http://192.168.3.32:3000` |
