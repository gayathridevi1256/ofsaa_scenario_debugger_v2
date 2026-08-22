# Deploy to 192.168.0.32 — Fresh Clone

## Step 1: Clone on server

```bash
ssh oracle@192.168.0.32
cd /opt
git clone <repo-url> scenario-debugger
cd scenario-debugger
```

You now have only source code. No `.env`, no `.venv`, no `node_modules`, no runtime folders.

## Step 2: Create .env

```bash
cp .env.example .env
nano .env   # edit passwords and paths
```

Minimum changes:
```ini
SECRET_KEY=<generate: python3 -c "import secrets; print(secrets.token_hex(32))">
CORS_ORIGINS=http://localhost:3000,http://192.168.0.32:3000
```

Rest of `.env.example` has sensible defaults already.

## Step 3: Install dependencies (one time)

```bash
# Python backend
cd /opt/scenario-debugger/backend
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv sync

# Node.js frontend
cd /opt/scenario-debugger/frontend-next
npm install
npm run build
```

If Python 3.13 or Node.js missing:
```bash
sudo dnf install -y python3.13 python3.13-devel nodejs npm
# Oracle Instant Client (needed for oracledb)
sudo dnf install -y oracle-instantclient-basic oracle-instantclient-devel
```

## Step 4: Create runtime folders

```bash
cd /opt/scenario-debugger
mkdir -p uploads outputs logs db pdf_reports
```

## Step 5: Start the app

Two terminals (or tmux):

**Terminal 1 — Backend:**
```bash
cd /opt/scenario-debugger/backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd /opt/scenario-debugger/frontend-next
npm run start    # production on port 3000
```

## Step 6: Open

```
http://192.168.0.32:3000
```

Login: `admin` / `changeme123` (change immediately)

---

## One-liner start with tmux

```bash
tmux new -s debugger \; \
  send-keys "cd /opt/scenario-debugger/backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000" Enter \; \
  split-window -h \; \
  send-keys "cd /opt/scenario-debugger/frontend-next && npm run start" Enter \;
```

`Ctrl+B D` to detach. `tmux attach -t debugger` to reattach.

---

## What a fresh clone gives you

| Present after clone | Missing (created in steps above) |
|---------------------|----------------------------------|
| `backend/` source code | `.env` (copy from `.env.example`) |
| `frontend-next/` source code | `backend/.venv/` (created by `uv sync`) |
| `package.json`, `package-lock.json` | `node_modules/` (created by `npm install`) |
| `.env.example` (template) | `frontend-next/.next/` (created by `npm run build`) |
| `deploy/` configs | `db/`, `logs/`, `outputs/`, `uploads/`, `pdf_reports/` |
