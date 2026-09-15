# OFSAA Scenario Debugger

A full-stack diagnostic tool for AML (Anti-Money Laundering) scenario analysts. When an OFSAA scenario runs but generates no alerts, this tool automatically diagnoses why — parsing the log file, re-executing the SQL against Oracle, splitting it into CTEs, and pinpointing the exact condition that is filtering out all rows.

---

## What It Does

OFSAA AML scenarios are complex SQL pipelines. When alerts stop generating, the root cause is almost always a CTE (Common Table Expression) returning zero rows due to a date mismatch, missing data, or a threshold condition. Manually tracing this takes hours.

This tool automates that investigation in six steps:

| Step | Name | What It Does |
|------|------|--------------|
| 1 | **Log Reader** | Parses the OFSAA log file — extracts scenario name, business date, threshold set ID, and embedded SQL |
| 2 | **Set Batch Date** | SSHs into the OFSAA server and sets the business date to match the log |
| 3 | **SQL Executer** | Runs the full scenario SQL against Oracle — if alerts are found, stops here (scenario is working) |
| 4 | **CTE Parser** | Splits the scenario SQL into individual CTEs and saves each as a separate `.sql` file |
| 5 | **CTE Executer** | Creates Oracle views for each CTE in order, validates row counts — stops at the first empty CTE |
| 6 | **SQL Diagnostics** | Runs granular diagnosis on ALL failing CTEs — identifies the exact JOIN condition, filter, or date range causing zero rows |

Results are streamed live to the browser via WebSocket as each step completes.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Browser (Next.js / React)                  │
│  Login → Upload .log file → Run → Live progress → Results   │
└───────────────────┬──────────────────────────────────────────┘
                    │  HTTP + WebSocket
┌───────────────────▼──────────────────────────────────────────┐
│              FastAPI Backend (Python 3.13)                   │
│  /api/auth/login  →  JWT authentication                      │
│  /api/upload      →  Save log file                           │
│  /api/jobs/run    →  Start pipeline (background task)        │
│  /api/ws/{job_id} →  WebSocket: stream live progress         │
│  /api/jobs        →  Job history                             │
│  /api/admin/*     →  User management, audit log              │
└───────┬───────────────────────────────────┬──────────────────┘
        │                                   │
┌───────▼───────┐                  ┌────────▼────────┐
│  SQLite DB    │                  │  Pipeline       │
│  users        │                  │  (6 Python      │
│  jobs         │                  │   scripts)      │
│  job_steps    │                  └────────┬────────┘
│  audit_log    │                           │
└───────────────┘               ┌───────────┴───────────┐
                                │                       │
                        ┌───────▼──────┐    ┌──────────▼──────┐
                        │  Oracle DB   │    │  OFSAA Server   │
                        │  (scenario   │    │  (SSH — set     │
                        │   SQL)       │    │   batch date)   │
                        └──────────────┘    └─────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend (new) | Next.js 14 App Router, React 18, CSS Variables |
| Frontend (old) | React 18, Vite, React Router |
| Backend (new) | FastAPI, Uvicorn, Python 3.13 — modular `app/` structure |
| Database | SQLite (job tracking, users, audit log) |
| External DB | Oracle (via `oracledb`) |
| SSH | Paramiko (set batch date on OFSAA server) |
| Auth | JWT (PyJWT), bcrypt password hashing |
| Real-time | WebSocket (asyncio) |
| Deployment | Nginx reverse proxy, systemd service |
| Package mgmt | `uv` (backend), `npm` (frontend) |

---

## Project Structure

```
scenario-debugger/
├── backend/app/              # New modular FastAPI structure
│   ├── main.py               # App factory (<50 lines)
│   ├── config.py             # Settings loader
│   ├── models/               # Pydantic schemas
│   │   ├── auth.py
│   │   ├── job.py
│   │   ├── upload.py
│   │   ├── batch.py
│   │   └── admin.py
│   ├── routers/              # API routes
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── upload.py
│   │   ├── jobs.py
│   │   ├── websocket.py
│   │   └── admin/
│   │       ├── users.py
│   │       └── audit.py
│   ├── services/             # Business logic
│   │   ├── auth_service.py
│   │   ├── job_service.py
│   │   ├── user_service.py
│   │   └── audit_service.py
│   ├── database/             # SQLite layer
│   │   ├── connection.py
│   │   ├── schema.py
│   │   ├── user_repo.py
│   │   ├── job_repo.py
│   │   └── audit_repo.py
│   ├── pipeline/             # Pipeline scripts
│   │   ├── orchestrator.py
│   │   ├── log_reader.py
│   │   ├── set_batch_date.py
│   │   ├── sql_executer.py
│   │   ├── cte_parser.py
│   │   ├── cte_executer.py
│   │   ├── sql_diagnostics.py
│   │   ├── generic_sql_diagnostics.py
│   │   ├── db_connect.py
│   │   ├── path_manager.py
│   │   ├── run_logger.py
│   │   └── scenario_config.py
│   └── websocket/
│       └── manager.py
│
├── frontend-next/            # New Next.js frontend
│   ├── app/                  # App Router pages
│   │   ├── layout.jsx
│   │   ├── page.jsx
│   │   ├── globals.css
│   │   ├── login/page.jsx
│   │   ├── dashboard/
│   │   │   ├── page.jsx
│   │   │   ├── upload-zone.jsx
│   │   │   └── job-history.jsx
│   │   ├── jobs/[jobId]/
│   │   │   ├── page.jsx
│   │   │   ├── pipeline-tracker.jsx
│   │   │   ├── cte-waterfall.jsx
│   │   │   └── root-cause-card.jsx
│   │   ├── batch/page.jsx
│   │   └── admin/
│   │       ├── users/page.jsx
│   │       └── audit/page.jsx
│   ├── components/
│   │   ├── layout/navbar.jsx
│   │   └── auth/
│   ├── hooks/
│   │   ├── use-auth.js
│   │   └── use-websocket.js
│   └── lib/api-client.js
│
├── old/                      # Legacy code (preserved)
│   ├── main.py, config.py, jobs.py, auth.py, orchestrator.py
│   ├── pipeline/
│   └── frontend/             # Old React + Vite app
│
├── deploy/
│   ├── nginx/
│   └── systemd/
├── pyproject.toml
└── README_DEPLOY.md
```

---

## Quick Start

### Prerequisites

- Python 3.13+
- Node.js 18+
- [`uv`](https://docs.astral.sh/uv/) — `pip install uv` or `winget install astral-sh.uv`
- Oracle Instant Client (for `oracledb`)
- Network access to your OFSAA Oracle DB and OFSAA server (SSH)

### 1. Configure environment

```bash
cd backend
copy .env.example .env
# Edit .env with your Oracle credentials, SSH details, etc.
```

Minimum required `.env` settings:

```env
APP_BASE_PATH=C:/scenario-debugger/data
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">

# Oracle DB
DB_USERNAME=your_oracle_user
DB_PASSWORD=your_oracle_password
DB_DSN=your_oracle_dsn

# OFSAA server SSH
SERVER_HOST=your_ofsaa_server_ip
SERVER_USERNAME=your_ssh_user
SERVER_PASSWORD=your_ssh_password
MANTAS_BATCH_PATH=/path/to/mantas/batch/on/server
```

### 2. Install dependencies

```bash
# Backend
cd backend
uv sync

# Frontend (new Next.js)
cd frontend-next
npm install
```

### 3. Start both servers

**Option A — New structure (recommended):**

```powershell
# Terminal 1: Backend
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Frontend
cd frontend-next
npm run dev
```

**Option B — Legacy (still works):**

```powershell
# From project root:
.\start.ps1
```

| Server | URL | Command |
|--------|-----|---------|
| Backend (new) | `http://127.0.0.1:8000` | `uv run uvicorn app.main:app --reload` |
| Backend (old) | `http://127.0.0.1:8000` | `uv run uvicorn main:app --reload` |
| Frontend (new) | `http://localhost:3000` | `cd frontend-next && npm run dev` |
| Frontend (old) | `http://localhost:5173` | `cd ../old/frontend && npm run dev` |

Open `http://localhost:3000` (new) or `http://localhost:5173` (old) in your browser.

**Default login:** `admin` / `changeme123` — change this immediately.

---

## Usage

1. **Login** with your credentials at `/login`
2. **Upload** an OFSAA scenario log file (`.log` or `.txt`, max 50 MB) by dragging onto the upload zone or clicking to browse
3. **Click Run** — the pipeline starts in the background; you are redirected to the Results page
4. **Watch live progress** — each of the 6 steps updates in real time via WebSocket
5. **Read the diagnosis** — when the pipeline completes, the root cause is displayed:
   - If alerts were generated: confirms the scenario is working
   - If no alerts: shows which CTEs returned zero rows and exactly why (missing data, date mismatch, threshold condition, etc.)
6. **Batch runs** — upload multiple log files and run all scenarios sequentially via the batch endpoint

---

## API Reference

All endpoints require a Bearer JWT token in the `Authorization` header (except `/api/auth/login`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/login` | Login — returns JWT token |
| `GET` | `/api/auth/me` | Get current user info |
| `POST` | `/api/upload` | Upload a `.log` or `.txt` file |
| `POST` | `/api/jobs/run` | Start pipeline for an uploaded file |
| `POST` | `/api/jobs/batch` | Start sequential batch of pipeline runs |
| `POST` | `/api/jobs/{id}/rerun` | Re-run an existing job |
| `GET` | `/api/jobs` | List jobs (analyst: own jobs; admin: all) |
| `GET` | `/api/jobs/{id}` | Get full job detail + step statuses |
| `WS` | `/api/ws/{job_id}` | WebSocket: live pipeline progress stream |
| `GET` | `/api/admin/users` | List all users (admin only) |
| `POST` | `/api/admin/users` | Create a user (admin only) |
| `DELETE` | `/api/admin/users/{id}` | Deactivate a user (admin only) |
| `GET` | `/api/admin/audit` | Get audit log (admin only) |
| `GET` | `/docs` | Interactive Swagger API docs |

### WebSocket events

The `/api/ws/{job_id}` stream sends JSON messages with an `event` field:

| Event | Meaning |
|-------|---------|
| `job_state` | Sent on connect — current job snapshot |
| `step_started` | A pipeline step has begun |
| `step_completed` | A step finished successfully |
| `step_failed` | A step failed with an error |
| `job_completed` | All steps done — includes root cause and results |
| `job_failed` | Pipeline stopped due to a fatal error |
| `ping` | Keep-alive heartbeat |

---

## Configuration Reference

All settings live in `backend/.env`. The backend reads them via `app/config.py`.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_BASE_PATH` | `C:/scenario-debugger` | Root directory for uploads, outputs, db, logs |
| `SECRET_KEY` | (must set) | JWT signing secret — generate with `secrets.token_hex(32)` |
| `JWT_EXPIRY_MINUTES` | `480` | Token lifetime (8 hours) |
| `API_HOST` | `127.0.0.1` | FastAPI listen address |
| `API_PORT` | `8000` | FastAPI listen port |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `MAX_UPLOAD_MB` | `50` | Maximum upload file size |
| `PIPELINE_TIMEOUT_SECONDS` | `300` | Max time per pipeline step |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DB_USERNAME` | (required) | Oracle database username |
| `DB_PASSWORD` | (required) | Oracle database password |
| `DB_DSN` | (required) | Oracle DSN / connection string |
| `SERVER_HOST` | (required) | OFSAA server IP/hostname for SSH |
| `SERVER_USERNAME` | (required) | SSH username |
| `SERVER_PASSWORD` | (required) | SSH password |
| `MANTAS_BATCH_PATH` | (required) | Path to Mantas batch script on OFSAA server |
| `LDAP_ENABLED` | `false` | Enable LDAP authentication |
| `LDAP_SERVER` | — | LDAP server URL (e.g. `ldap://bankldap:389`) |
| `LDAP_BASE_DN` | — | LDAP base DN |
| `LDAP_BIND_DN` | — | LDAP service account DN |
| `LDAP_BIND_PASSWORD` | — | LDAP service account password |

---

## User Roles

| Role | Permissions |
|------|------------|
| `admin` | Full access — user management, audit log, all jobs |
| `analyst` | Can upload files, run pipelines, view own job results |

Users are created by an admin via the UI (Admin panel) or the API. Passwords are hashed with bcrypt and never stored in plain text.

---

## Database Schema

SQLite database at `<APP_BASE_PATH>/db/scenario_debugger.db`.

**`users`** — authentication and roles  
**`jobs`** — every pipeline run (status, scenario name, batch date, root cause, result JSON)  
**`job_steps`** — per-step status for each job (running / completed / failed + output)  
**`audit_log`** — immutable record of every action (logins, uploads, pipeline runs, user changes)

---

## Output Files

For each pipeline run, outputs are written to `<APP_BASE_PATH>/outputs/<ScenarioName>/`:

```
outputs/
└── SCENARIO_NAME/
    ├── metadata.json          # Extracted log metadata
    ├── extracted_queries/
    │   └── dataset_query_*.sql  # Full scenario SQL from the log
    └── parsed_ctes/
        ├── cte_01_<name>.sql    # Individual CTEs
        ├── cte_02_<name>.sql
        ├── ...
        └── final_query.sql      # The final SELECT after all CTEs
```

---

## Production Deployment (Linux)

See [README_DEPLOY.md](README_DEPLOY.md) for the full step-by-step guide covering:

- Creating a dedicated service user
- Configuring `.env` for production
- Installing Python and Node dependencies
- Building the React frontend
- SSL certificate setup (bank CA or self-signed)
- Nginx reverse proxy configuration
- systemd service setup (auto-start on boot)
- Log monitoring and troubleshooting

**Quick summary:**

```bash
# Backend runs as a systemd service
sudo systemctl start scenario-debugger

# Nginx proxies HTTPS → FastAPI + serves React static files
# https://scenario-debugger.yourbank.com → http://127.0.0.1:8000

# Check status
sudo systemctl status scenario-debugger
curl http://127.0.0.1:8000/api/health
```

---

## Security Notes

- JWT tokens expire after 8 hours (configurable via `JWT_EXPIRY_MINUTES`)
- Passwords are hashed with bcrypt — never stored in plain text
- The `.env` file should be `chmod 600` and owned by the service user
- Change `SECRET_KEY` from the default before deploying
- Change the default `admin / changeme123` password immediately after first login
- In production, set `CORS_ORIGINS` to your exact domain (not `*`)
- The audit log records every login attempt, upload, and pipeline run
- File uploads are restricted to `.log` and `.txt` extensions, max 50 MB

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Backend won't start | Check `.env` exists in `backend/` and all required variables are set |
| Oracle connection fails | Verify `DB_USERNAME`, `DB_PASSWORD`, `DB_DSN` and that Oracle Instant Client is installed |
| SSH step fails | Verify `SERVER_HOST`, `SERVER_USERNAME`, `SERVER_PASSWORD`, and `MANTAS_BATCH_PATH` |
| No SQL found in log | The log file may be truncated or from a different OFSAA version — check the file manually |
| WebSocket disconnects | Ensure Nginx has `Upgrade` and `Connection` proxy headers set (see `nginx/` config) |
| Login fails (correct password) | `SECRET_KEY` is not set in `.env` |
| File upload 413 error | Increase `MAX_UPLOAD_MB` in `.env` and `client_max_body_size` in nginx config |
| Pipeline cached result returned | Pass `?force=true` to `/api/jobs/run` to skip the cache |

---

## License
