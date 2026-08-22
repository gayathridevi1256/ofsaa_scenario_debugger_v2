# OFSAA Scenario Debugger
## Infrastructure, Architecture & Security Overview

---

### 1. Problem Statement

OFSAA AML scenarios are complex SQL pipelines. When a scenario runs but produces **zero alerts**, an analyst must manually trace through nested CTEs, date filters, JOIN conditions, and threshold logic to find the root cause — a process that takes hours.

This tool **automates that investigation** end-to-end by re-executing the scenario SQL and diagnosing which condition filters out all rows.

---

### 2. Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| **CPU** | 2 cores | 4 cores |
| **RAM** | 4 GB | 8 GB |
| **Disk Space** | 10 GB free | 50 GB (for logs/outputs storage) |
| **OS** | Windows Server 2016+ / RHEL 7+ / Ubuntu 20.04+ | RHEL 8+ / Ubuntu 22.04+ |
| **Network** | LAN access to OFSAA App Server & Oracle DB | Same subnet for low latency |

> No GPU, no cloud dependency, no special hardware required. Runs on standard VM or bare-metal.

---

### 3. Software & Technologies

| Category | Technology |
|---|---|
| **Runtime** | Python 3.13 |
| **Web Framework** | FastAPI (Python) |
| **User Interface** | Next.js 16 / React 19 (browser-based) |
| **Local Database** | SQLite (self-contained, zero-config) |
| **Target Database** | Oracle 19c+ (read-only access) |
| **Remote Access** | SSH (connect to OFSAA App Server) |
| **Authentication** | JWT + bcrypt + LDAP/AD (optional) |
| **Real-time Updates** | WebSocket |
| **Reporting** | PDF generation (on-demand) |
| **Deployment** | Nginx + systemd (Linux) or Windows Service |

---

### 4. Unified Pipeline Flow

```
User uploads .log file
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 1: Log Reader                                                  │
│ • Read .log file                                                    │
│ • Extract metadata: scenario name, business date, threshold set ID  │
│ • Detect scenario type (Non-Function vs Function)                   │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 2: Set Batch Date                                              │
│ • SSH into OFSAA App Server                                         │
│ • Align business date with log's date context                       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
┌──────────────────────────────┐  ┌──────────────────────────────────┐
│ NON-FUNCTION (160 scenarios) │  │ FUNCTION (13 scenarios)          │
├──────────────────────────────┤  ├──────────────────────────────────┤
│ STEP 3: SQL Executer         │  │ STEP 3: Resolve & Execute        │
│ • Load dataset SQL           │  │ • Load function SQL + params     │
│ • Execute directly on Oracle │  │ • Extract SQL between IS/BEGIN   │
│ • Check for alerts           │  │ • Auto-match params to SQL       │
│                              │  │ • Execute resolved SQL on Oracle │
│                              │  │ • Check for alerts               │
└──────────────┬───────────────┘  └───────────────┬──────────────────┘
               │                                  │
     ┌─────────┴─────────┐              ┌─────────┴─────────┐
     │                   │              │                   │
     ▼                   ▼              ▼                   ▼
  ✅ Alerts          ❌ No alerts    ✅ Alerts          ❌ No alerts
  STOP               continue       STOP               continue
                         │                                  │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────────┐
                         │ STEP 4: CTE Parser               │
                         │ • Split SQL into individual CTEs │
                         │ • Save each as .sql file         │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         ┌──────────────────────────────────┐
                         │ STEP 5: CTE Executer             │
                         │ • Create temporary Oracle views  │
                         │ • Count rows per CTE             │
                         │ • Identify ALL empty CTEs        │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         ┌──────────────────────────────────┐
                         │ STEP 6: SQL Diagnostics          │
                         │ • JOIN analysis                  │
                         │ • WHERE clause analysis          │
                         │ • HAVING condition analysis      │
                         │ • Date range analysis            │
                         │ • Threshold value check          │
                         │ • Source data availability check │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         ┌──────────────────────────────────┐
                         │ 📋 Root Cause + Condition        │
                         │   + Line Number + Row Counts     │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         📋 Results displayed in browser UI
```

**What it diagnoses:**
- Date mismatches between log and database
- Missing reference data (lookup tables, codes, thresholds)
  *Example: A scenario filters by `p_high_risk_country_cd IN (SELECT code FROM AML_COUNTRY_LIST)` but the lookup table `AML_COUNTRY_LIST` is empty or missing the required codes — result: zero rows.*
- Overly restrictive WHERE conditions
- Silent JOIN failures dropping rows
- Empty source tables or views
- Function parameter mismatches (auto-resolved via fuzzy matching)

**Real-Time Progress:**
Pipeline Step → WebSocket broadcast → Live UI updates as each step completes. No page refresh needed.

---

### 5. Security

#### No Internet Required

This application is designed to run **entirely within your internal network**. It does not:
- Make any outbound internet calls
- Use any external APIs or cloud services
- Require internet access to function
- Send data outside your organization

All processing happens on-premises, within your firewall.

#### Security Controls

| Control | How It's Secured |
|---|---|
| **Authentication** | Username/password with JWT tokens. Sessions expire after 8 hours (configurable). |
| **Password Policy** | bcrypt hashed passwords (one-way). Never stored or transmitted in plain text. |
| **Role-Based Access** | Two roles — Admin (full control) and Analyst (view own jobs only). |
| **LDAP/AD Ready** | Optional integration with corporate Active Directory. No local passwords needed if LDAP is enabled. |
| **Network Binding** | Application binds to `127.0.0.1` — not exposed on network. Only accessible via local Nginx proxy. |
| **No Internet** | Fully air-gapped capable. Zero external dependencies at runtime. |
| **Audit Logging** | Every action logged — who logged in, what job was run, when, from which IP. Tamper-proof history. |
| **Database Security** | SQLite database outside web root. All queries use parameterized statements — immune to SQL injection. |
| **Oracle Access** | Read-only connection only. Cannot modify production data. |
| **File Safety** | Uploads restricted to `.log` / `.txt` files only, size-capped at 50 MB. Malicious files rejected. |
| **Secrets Protection** | All credentials (Oracle, SSH, JWT key) stored in environment file outside source code. Excluded from version control. |
| **Session Hardening** | Auto-expiring tokens. No sessions stored server-side. No cookies used. |
| **Transport Security** | Nginx frontend provides TLS/HTTPS encryption for all browser traffic. |
| **Log Protection** | Rotating log files (10 MB × 5 backups). Old logs auto-purged. |

#### Internal Network Diagram

```
 ┌─────────────────────────────────────────────────────────┐
│                   YOUR INTERNAL NETWORK                    │
│                                                            │
│  ┌──────────┐      ┌──────────────┐      ┌─────────────┐ │
│  │ Analyst  │─────▶│   Scenario   │─────▶│   Oracle    │ │
│  │ Browser  │ HTTPS│   Debugger   │  SQL │   Database  │ │
│  │          │◀─────│   Server     │◀─────│ (Read Only) │ │
│  └──────────┘      └──────┬───────┘      └─────────────┘ │
│                           │ SSH                            │
│                           ▼                                │
│                    ┌──────────────┐                        │
│                    │    OFSAA     │                        │
│                    │ App Server   │                        │
│                    └──────────────┘                        │
│                                                            │
│  ════════════════════════════════════════════════════════ │
│                         FIREWALL                            │
│  ════════════════════════════════════════════════════════ │
│                                                            │
│              NO INTERNET CONNECTION REQUIRED                │
│                                                            │
└─────────────────────────────────────────────────────────┘
```

---

### 6. Deployment

| Environment | Approach |
|---|---|
| **Development** | Single command — `uv run uvicorn` (backend) + `npm run dev` (frontend) |
| **Production (Linux)** | Nginx reverse proxy + systemd service. Auto-restart on crash. |
| **Production (Windows)** | Windows Service or scheduled task with Nginx/IIS as reverse proxy. |

---

*Document prepared for client review — July 2026*