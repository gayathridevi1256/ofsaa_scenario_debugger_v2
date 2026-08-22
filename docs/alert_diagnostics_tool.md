# OFSAA Alert Diagnostics Tool

---

## 1. Project Description

**Automated root cause analysis for OFSAA AML scenarios that fail to generate alerts.**

The tool ingests OFSAA execution log files, automatically extracts SQL queries and metadata, resolves Oracle function parameters, executes against the database, and performs granular condition-level diagnosis to identify the exact cause of missing alerts — including data completeness issues, threshold misconfigurations, and SQL logic errors.

---

## 2. Architecture Diagram

```
┌──────────┐     HTTP/WS      ┌─────────────────────────────────────────┐
│  React   │◄────────────────►│           FastAPI Backend               │
│  Frontend│                  │                                         │
│          │                  │  ┌───────────────────────────────────┐  │
│ Upload   │                  │  │  Pipeline (6 Steps)              │  │
│ Tracker  │                  │  │                                   │  │
│ Results  │                  │  │  1. Log Reader → extract SQL     │  │
└──────────┘                  │  │  2. Set Date   → SSH to OFSAA    │  │
                              │  │  3. SQL Exec   → Oracle query    │  │
                              │  │  4. CTE Parser → split CTEs      │  │
                              │  │  5. CTE Exec   → Oracle views    │  │
                              │  │  6. Diagnose   → find root cause │  │
                              │  └───────────────────────────────────┘  │
                              │                                         │
                              │  ┌────────────┐ ┌─────────┐ ┌────────┐ │
                              │  │ oracledb   │ │ paramiko│ │SQLite  │ │
                              │  │ (Oracle)   │ │ (SSH)   │ │ (App)  │ │
                              │  └────────────┘ └─────────┘ └────────┘ │
                              └─────────────────────────────────────────┘
                                       │            │
                                       ▼            ▼
                               ┌────────────┐ ┌───────────┐
                               │ Oracle DB  │ │ OFSAA     │
                               │ ALL_SOURCE │ │ Server    │
                               │ User data  │ │ (SSH)     │
                               └────────────┘ └───────────┘
```

---

## 3. Functional Flowchart

```
┌────────────────────────────────────────────────────────────────────┐
│                        UPLOAD LOG FILE                             │
│                   (OFSAA .log file via browser)                    │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STEP 1: LOG READER                              │
│                                                                     │
│  Extract Metadata               Extract SQL Queries                │
│  ┌──────────────────────┐      ┌──────────────────────────┐        │
│  │ • Job Description    │      │ • ALL "After expansion"  │        │
│  │ • Business Date      │      │   SQL blocks (reference) │        │
│  │ • Threshold Set ID   │      │ • ALL "Preparing to      │        │
│  │ • SCNRO_ID           │      │   select from dataset"   │        │
│  └──────────────────────┘      │   SQL blocks with IDs    │        │
│                                └──────────────────────────┘        │
│                                                                     │
│  ┌──────────────────────────────────────────────┐                  │
│  │  SCENARIO CLASSIFICATION                     │                  │
│  │  ┌────────────┐         ┌─────────────────┐  │                  │
│  │  │ Standard   │         │ Function-Based  │  │                  │
│  │  │ (1 ref+ds) │         │ (N ref+N ds +   │  │                  │
│  │  │            │         │  F_*.sql + JSON)│  │                  │
│  │  └────────────┘         └─────────────────┘  │                  │
│  └──────────────────────────────────────────────┘                  │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STEP 2: SET BATCH DATE                          │
│               SSH → OFSAA: end batch → set date → start batch     │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STEP 3: EXECUTE SQL                             │
│                                                                     │
│  ┌───────────────────────┐    ┌──────────────────────────────┐    │
│  │ STANDARD              │    │ FUNCTION                     │    │
│  │                       │    │                              │    │
│  │ Run dataset query     │    │ Load F_*.sql + params.json  │    │
│  │                       │    │ Extract SQL (IS→BEGIN)      │    │
│  │                       │    │ Replace p_* with values     │    │
│  │                       │    │ Execute resolved SQL        │    │
│  │                       │    │ 0 rows → save as dataset    │    │
│  └──────────┬────────────┘    └──────────────┬───────────────┘    │
│             │                                │                     │
│             └──────────┬─────────────────────┘                     │
│                        ▼                                           │
│               ┌────────────────┐                                   │
│               │  HAS ALERTS?   │                                   │
│               │  (Rows > 0?)   │                                   │
│               └──┬─────────┬───┘                                   │
│         ┌────────┘         └────────┐                              │
│         ▼                           ▼                              │
│   ┌──────────┐            ┌──────────────────┐                    │
│   │  STOP    │            │ Continue to CTE  │                    │
│   │ (Success)│            │ Analysis (Steps   │                    │
│   └──────────┘            │ 4-6)             │                    │
│                           └──────────────────┘                    │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STEPS 4-6: CTE ANALYSIS                         │
│                                                                     │
│  4. CTE PARSER: Parse WITH clause → individual CTE files          │
│  5. CTE EXECUTER: Create Oracle views → validate row counts       │
│  6. DIAGNOSTICS: Progressive condition analysis:                   │
│     • Check dependencies → tables/views exist                     │
│     • Diagnose JOINs → add one-by-one, find killer                │
│     • Diagnose WHERE → remove one condition at a time             │
│     • Diagnose HAVING → add conditions progressively              │
│     • Unknown fallback → inner subquery analysis                  │
│     • Line number detection in dataset query                     │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    OUTPUT IN UI                                    │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ ❌ ROOT CAUSE FOUND                                         │  │
│  │                                                             │  │
│  │ CTE: final_query/fq_inner_28                               │  │
│  │ Failure Type: HAVING                                        │  │
│  │ Killer Condition: ('N' = 'Y' or sum(t.Not_EFT_Trans)<>0)  │  │
│  │ Dataset Query Line: 667                                    │  │
│  │ Likely Cause: Thresholds set too high                      │  │
│  │ *** Killer condition at dataset query line 667 ***        │  │
│  └─────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. Technical Components

### Backend
| Component | Technology | Purpose |
|-----------|-----------|---------|
| API Framework | FastAPI (Python) | REST endpoints + WebSocket |
| ASGI Server | Uvicorn | HTTP/WS server |
| Database Driver | oracledb | Oracle connection |
| SSH Client | paramiko | OFSAA server connection |
| SQL Parser | sqlglot | CTE extraction |
| Auth | PyJWT + bcrypt | Login security |
| App Database | SQLite | Jobs, users, audit |

### Frontend
| Component | Technology | Purpose |
|-----------|-----------|---------|
| UI Framework | React 19 | Web interface |
| Build Tool | Vite 6 | Dev server + builds |
| Routing | react-router-dom | Page navigation |

### Pipeline Modules
| Module | File | Responsibility |
|--------|------|----------------|
| Log Reader | `log_reader.py` | Extract metadata + SQL + function bodies + parameters |
| Set Batch Date | `set_batch_date.py` | SSH to OFSAA, set business date |
| SQL Executer | `sql_executer.py` | Execute dataset/resolved SQL, check alerts |
| CTE Parser | `cte_parser.py` | Parse WITH clause into individual CTE files |
| CTE Executer | `cte_executer.py` | Create Oracle views, validate row counts |
| SQL Diagnostics | `sql_diagnostics.py` | 6-step progressive condition analysis |

---

## 5. Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 cores @ 2.5 GHz | 8 cores @ 3.0 GHz |
| RAM | 8 GB | 16 GB |
| Storage | 10 GB free | 50 GB free |
| OS | Windows Server 2019 / Ubuntu 20.04 | Windows Server 2022 / Ubuntu 22.04 |
| Python | 3.13+ | 3.13 |
| Node.js | 18+ | 20 LTS |

### Network
| Connection | Port | Purpose |
|-----------|------|---------|
| Oracle DB | 1521 | SQL execution |
| OFSAA Server | 22 (SSH) | Set batch date |
| Browser → Backend | 8000 | API + WebSocket |
| Frontend Dev | 5173 | Dev server only |

---

## 6. Issues Detectable

| Category | What It Finds |
|----------|--------------|
| **Threshold Configuration** | Range too narrow/wide, wrong min/max, missing bindings |
| **Data Completeness** | Empty source tables, calendar gaps, missing jurisdiction data |
| **Date/Calendar** | Wrong batch date, lookback period has no data, date filter too restrictive |
| **SQL Logic** | JOIN eliminates rows, conflicting WHERE conditions, HAVING too strict |
| **Parameter Mismatch** | Wrong parameter names between query and function body |
| **Environment** | Missing tables/views/functions, permission errors |
| **NULL/Data Quality** | NULL values causing exclusion, orphaned reference data |

---

## 7. Advantages

| Area | Manual Process | Automated Tool |
|------|---------------|----------------|
| Time | 2-4 hours per log | 10-15 minutes |
| SQL Extraction | Copy-paste errors possible | 100% accurate regex extraction |
| Parameter Resolution | Manual lookup, error-prone | Auto-resolved from log |
| Condition Testing | Random trial-and-error | Systematic remove/add one approach |
| Line Number | Not available | Exact line pinpointed |
| Threshold Suggestions | Manual data queries | Auto-queried from KDD_TSHLD |
| Reproducibility | Varies by analyst | Same log → same result |
| Knowledge | Depends on senior staff | Captured in code |

---

## 8. Data Flow (Input → Output)

```
OFSAA .log file
    ↓
Extracted Queries (.sql files)
    ↓
Oracle ALL_SOURCE function bodies (F_*.sql)
    ↓
Parameters JSON (key-value pairs)
    ↓
Resolved SQL (parameters replaced)
    ↓
Oracle execution → alerts check
    ↓
CTE analysis (if no alerts)
    ↓
Root cause with line number
    ↓
Displayed in React UI
```
