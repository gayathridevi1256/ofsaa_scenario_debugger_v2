# OFSAA Scenario Debugger — Codebase Guide for AI Agents

## Project Overview
Full-stack AML diagnostic tool. Parses OFSAA log files, re-executes scenario SQL against Oracle, splits into CTEs, pinpoints why zero alerts were generated (date mismatch, missing data, threshold condition, JOIN failure).

**Stack:** FastAPI (Python 3.13) + Next.js 16 (React 18) + SQLite + Oracle + SSH (Paramiko)

## Directory Structure

```
Scenario_Debugger/
├── backend/                         # Python FastAPI backend
│   ├── pyproject.toml               # Dependencies (uv)
│   ├── app/
│   │   ├── main.py                  # FastAPI entry, lifespan, CORS, routes
│   │   ├── config.py                # .env loader, APP_BASE_PATH, dir creation
│   │   ├── database/
│   │   │   ├── connection.py        # SQLite get_db() context manager
│   │   │   ├── schema.py            # CREATE TABLE (users, jobs, job_steps, audit_log)
│   │   │   ├── user_repo.py         # User CRUD + ensure_admin_exists()
│   │   │   ├── job_repo.py          # Job CRUD + step status updates
│   │   │   └── audit_repo.py        # Audit log inserts
│   │   ├── models/                  # Pydantic request/response models
│   │   ├── routers/
│   │   │   ├── auth.py              # POST /login, GET /me
│   │   │   ├── jobs.py              # Run, batch, rerun, list, get, PDF report
│   │   │   ├── upload.py            # File upload
│   │   │   ├── websocket.py         # WS /api/ws/{job_id}
│   │   │   ├── health.py            # Health check
│   │   │   └── admin/               # User management, audit log
│   │   ├── services/
│   │   │   ├── auth_service.py      # JWT, bcrypt, LDAP stub
│   │   │   ├── job_service.py       # Pipeline runner, batch, WS queue forward
│   │   │   ├── pdf_service.py       # PDF report generation (lang-aware)
│   │   │   ├── translator.py        # Google Translate API (cached, for PDF i18n)
│   │   │   ├── user_service.py      # User operations
│   │   │   └── audit_service.py     # Audit operations
│   │   ├── websocket/
│   │   │   └── manager.py           # Connection manager + event buffer for replay
│   │   └── pipeline/
│   │       ├── orchestrator.py      # 6-step pipeline with timeout
│   │       ├── log_reader.py        # Step 1: Parse log -> metadata + SQL
│   │       ├── set_batch_date.py    # Step 2: SSH + set business date
│   │       ├── sql_executer.py      # Step 3: Run SQL against Oracle
│   │       ├── cte_parser.py        # Step 4: Split into CTE .sql files
│   │       ├── cte_executer.py      # Step 5: Create views, find empty CTEs
│   │       ├── sql_diagnostics.py   # Step 6: Root-cause diagnosis (4142 lines)
│   │       ├── generic_sql_diagnostics.py  # Alternate generic diagnosis path
│   │       ├── db_connect.py        # Oracle connection with retry
│   │       ├── path_manager.py      # Output dir management
│   │       ├── run_logger.py        # Structured per-run log writer
│   │       ├── scenario_config.py   # Scenario ID constants
│   │       └── steps/               # Additional step helpers
│   ├── .venv/                       # Virtual env
│   ├── uploads/                     # Uploaded log files (at APP_BASE_PATH)
│   └── outputs/                     # Pipeline output per scenario
├── frontend-next/                   # Next.js frontend (App Router)
│   ├── app/
│   │   ├── page.jsx                 # Root redirect -> /dashboard
│   │   ├── layout.jsx               # Root layout + AuthProvider
│   │   ├── login/page.jsx           # Login form
│   │   ├── dashboard/
│   │   │   ├── page.jsx             # Upload + job history
│   │   │   ├── upload-zone.jsx      # File upload component
│   │   │   ├── job-history.jsx      # Job history table
│   │   │   └── batch/page.jsx       # Batch run summary
│   │   ├── jobs/[jobId]/
│   │   │   ├── page.jsx             # Job detail + lang selector + export
│   │   │   ├── pipeline-tracker.jsx # 6-step progress visualization
│   │   │   ├── cte-waterfall.jsx    # CTE row count waterfall
│   │   │   └── root-cause-card.jsx  # Diagnostic results + resolved names
│   │   └── admin/                   # Admin pages
│   ├── components/layout/navbar.jsx
│   ├── hooks/
│   │   ├── use-auth.js              # Auth context + login/logout
│   │   └── use-websocket.js         # WebSocket with auto-reconnect
│   ├── lib/api-client.js            # Axios API client
│   ├── store/                       # (empty — no state lib needed)
│   └── package.json
├── .env                             # Environment config
├── start.ps1                        # Windows dev launcher (updated paths)
└── AGENTS.md                        # This file
```

## API Routes

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/auth/login` | Login -> JWT token |
| `GET` | `/api/auth/me` | Current user info |
| `POST` | `/api/upload` | Upload .log/.txt file |
| `POST` | `/api/jobs/run` | Start pipeline for uploaded file |
| `POST` | `/api/jobs/batch` | Sequential batch of runs |
| `POST` | `/api/jobs/{id}/rerun` | Re-run existing job |
| `GET` | `/api/jobs` | List jobs |
| `GET` | `/api/jobs/{id}` | Job detail + step statuses |
| `GET` | `/api/jobs/{id}/report?lang=en\|pt` | **PDF report with language option** |
| `WS` | `/api/ws/{job_id}` | Live progress stream (buffered replay) |
| `GET` | `/api/admin/users` | List users (admin) |
| `POST` | `/api/admin/users` | Create user (admin) |
| `DELETE` | `/api/admin/users/{id}` | Deactivate user (admin) |
| `GET` | `/api/admin/audit` | Audit log (admin) |
| `GET` | `/docs` | Swagger docs |

## Pipeline Steps (in `app/pipeline/orchestrator.py`)

1. **Log Reader** — Parse log -> scenario name, date, SQL
2. **Set Batch Date** — SSH into OFSAA server, set business date
3. **SQL Executer** — Run SQL against Oracle, check alert count
4. **CTE Parser** — Split SQL into individual CTE .sql files
5. **CTE Executer** — Create Oracle views per CTE, find first empty one
6. **SQL Diagnostics** — Granular root-cause diagnosis

**Pipeline timeout:** Each step has `PIPELINE_TIMEOUT_SECONDS` timeout (default 300s from `.env`). Hung steps auto-fail.

## Database (SQLite)

Tables: `users`, `jobs`, `job_steps`, `audit_log`
- DB path: `<APP_BASE_PATH>/db/scenario_debugger.db`
- Accessed via `get_db()` context manager in `app/database/connection.py`

## Key Backend Dependencies

```
fastapi, uvicorn[standard], oracledb, paramiko, pyjwt, bcrypt, sqlglot, sqlparse,
websockets, pydantic, python-multipart, reportlab, requests, python-dotenv
```

## Frontend Key Details

- **Auth:** `AuthContext` + `useAuth()` in `hooks/use-auth.js`. JWT in localStorage, sent via Axios interceptor.
- **WebSocket:** `useWebSocket(jobId, handlers)` in `hooks/use-websocket.js` — receives `step_started`, `step_completed`, `step_failed`, `job_completed`, `job_failed` events. Auto-reconnects up to 5 times.
- **API client:** `api` (Axios) in `lib/api-client.js`. Modules: `authAPI`, `filesAPI`, `jobsAPI`, `adminAPI`.
- **Language selector:** Dropdown (English / Português Brasil) on job detail page. Passes `lang` to PDF export API.
- **Diagnostic display:** Root cause cards show CTE name, failure type badge, failing condition (with aliases), resolved table names (alias→table mapping), likely cause, verification, WHERE/HAVING analysis, threshold suggestions, UNION ALL branch counts with FROM table names.

## How to Run

```powershell
# Backend only (from backend/)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app

# Frontend only (from frontend-next/)
npm run dev

# Both (from root)
.\start.ps1
```

Backend: `http://127.0.0.1:8000` | Frontend: `http://localhost:3000`
Default login: `admin` / `changeme123`

## Config (`.env` at root)

Key vars: `APP_BASE_PATH`, `SECRET_KEY`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DSN`, `SERVER_HOST`, `SERVER_USERNAME`, `SERVER_PASSWORD`, `MANTAS_BATCH_PATH`, `LDAP_ENABLED`, `JWT_EXPIRY_MINUTES`, `PIPELINE_TIMEOUT_SECONDS`, `CORS_ORIGINS`, `OUTPUT_BASE_PATH`

## Conventions

- Backend: FastAPI async routes, Pydantic models, JWT dependency injection
- Pipeline state passed as dict with keys: `step`, `status`, `result`, `error`, `job_id`
- WebSocket messages: `event` field — `step_started`, `step_completed`, `step_failed`, `job_completed`, `job_failed`, `ping`
- Frontend: functional components, Next.js App Router, Axios, CSS variables
- SQLite connection per request, simple functions (no ORM)
- `--reload-dir app` limits uvicorn watch to app/ only (avoids .venv/ reloads)

## Important Bugfixes Applied

| # | Issue | File | Fix |
|---|-------|------|-----|
| 1 | `_find_condition_line_in_sql` reported wrong line number | `sql_diagnostics.py`, `generic_sql_diagnostics.py` | Added `NOT...IS NULL` → `IS NOT NULL` normalization, header stripping, compound condition splitting, wider multi-line window |
| 2 | `_split_and_conditions` grouped 4 conditions as 1 | `sql_diagnostics.py:474` | Removed faulty `i+klen` isalnum check; stripped keyword to bare token |
| 3 | `_likely_cause` said "data issue" for thresholds | `sql_diagnostics.py:547` | Moved `>=`/`<=` check BEFORE ` IN (` check |
| 4 | `_resolve_aliases` couldn't find aliases in nested SQL | `sql_diagnostics.py:2351` | Rewrote to use `re.finditer` on ALL FROM/JOIN clauses, not just first |
| 5 | `_verify_killer_source` regex failed on DECODE | `sql_diagnostics.py:2451` | Added fallback pattern for function-wrapped columns |
| 6 | Correlated subqueries caused `execution_error` | `sql_diagnostics.py:3319` | Try direct execution first; skip correlated subqueries gracefully |
| 7 | WebSocket events lost before client connects | `manager.py`, `job_service.py` | Event buffer replays missed events on WS connect |
| 8 | Pipeline hung forever with no timeout | `orchestrator.py:561` | Added `asyncio.wait_for` step timeout |
| 9 | `--reload` restarted mid-pipeline on package changes | `start.ps1` | Added `--reload-dir app` |
| 10 | `DataAvailabilitySection` hidden without `explanation` | `root-cause-card.jsx:228` | Changed guard to check ANY data present |
