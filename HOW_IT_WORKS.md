# OFSAA Scenario Debugger — How It Works

## Problem Statement

OFSAA AML scenarios are complex SQL pipelines. When a scenario runs but produces **zero alerts**, an analyst must manually trace through nested CTEs, date filters, JOIN conditions, and threshold logic to find the root cause — a process that takes hours.

This tool **automates that investigation** end-to-end by re-executing the scenario SQL and diagnosing which condition filters out all rows.

---

## Two Scenario Types

| Type | Description | Count | Detection |
|------|-------------|-------|-----------|
| **Non-Function** | SQL query with WITH clause (CTEs) | 160 | Scenario ID does NOT match function patterns |
| **Function** | PL/SQL function with parameter placeholders | 13 | Scenario ID matches `FUNCTION_SCENARIO_IDS` |

### Function Scenario IDs

| Category | IDs | Pattern |
|----------|-----|---------|
| Functions (12) | `117350046`, `117350005`, `114000065`, `114000071`, `118860034`, `118860035`, `118725006`, `118860031`, `116000046`, `118860028`, `118860029`, `118860030` | `@MINER@.F_*` |
| Pipeline (1) | `116000065` | `@MINER@.ANOMATMEXCESS_PIPELINE` |

---

## Unified Pipeline Flow — Both Scenario Types

```
User uploads .log file
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 1: Log Reader                                                  │
│ • Read .log file                                                    │
│ • Extract metadata: scenario name, business date, threshold set ID  │
│ • Extract SCNRO_ID                                                  │
│ • Detect scenario type via scenario_config.py                       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
┌──────────────────────────────┐  ┌──────────────────────────────────┐
│ NON-FUNCTION (160 scenarios) │  │ FUNCTION (13 scenarios)          │
├──────────────────────────────┤  ├──────────────────────────────────┤
│ STEP 3: SQL Executer         │  │ STEP 3: Resolve & Execute        │
│ • Load dataset SQL           │  │ • Load F_*.sql + params JSON     │
│ • Execute directly           │  │ • Extract SQL between IS/BEGIN   │
│ • Check for alerts           │  │ • Normalize JSON keys → match    │
│                              │  │   SQL params & replace            │
│                              │  │ • Execute resolved SQL           │
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
                         │ • WHERE analysis                 │
                         │ • HAVING analysis                │
                         │ • Date analysis                  │
                         │ • Threshold check                │
                         │ • Data check                     │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         ┌──────────────────────────────────┐
                         │ 📋 Root cause + condition        │
                         │ + line number + row counts       │
                         └────────────────┬─────────────────┘
                                          │
                                          ▼
                         📋 Results displayed in browser UI
```

---

## Step 3 Detail: Function Parameter Resolution

**The Problem:** JSON keys don't match SQL param names.

| JSON Key | SQL Param | Difference |
|----------|-----------|------------|
| `p_Incl_MI_Trxn_Prdct_Type_Lst` | `p_Incl_MI_Prdct_Type_Lst` | `Trxn` removed |
| `p_Incl_Cash_Trxn_Prdct_Type_Lst` | `p_Incl_Csh_Prdct_Type_Lst` | `Trxn` removed, `Cash`→`Csh` |

**Resolution Algorithm:**

1. **Extract SQL params** from function signature
2. **Normalize both sides** — expand abbreviations (csh→cash, prdct→product), strip filler words (transaction, list, type), remove underscores
3. **Match & replace** — exact normalized match first, Levenshtein similarity fallback (>0.6 threshold)

---

## Output Directory Structure

```
outputs/SCENARIO_NAME/
├── metadata.json              # Scenario metadata + SCNRO_ID
├── extracted_queries/
│   ├── F_*.sql                # Function body (function flow only)
│   ├── parameters_*.json      # Parameter values (function flow only)
│   ├── *_reference_*.sql      # Reference queries
│   └── *_dataset_*.sql        # Dataset queries (non-function) / resolved SQL (function)
├── parsed_ctes/
│   ├── 001_<name>.sql         # Individual CTEs
│   ├── 002_<name>.sql
│   ├── ...
│   └── final_query.sql
└── run_log_*.txt              # Structured per-run log
```

---

## Real-Time Progress Streaming

Pipeline Step → `asyncio.Queue` → WebSocket broadcast → React UI update

| WebSocket Event | When | Data |
|-----------------|------|------|
| `step_started` | Step begins | step name, message |
| `step_completed` | Step succeeds | step name, output |
| `step_failed` | Step errors | step name, error |
| `job_completed` | All steps done | root cause, alerts, results |
| `job_failed` | Pipeline crashed | error reason |

---

## Common Issues & Fixes

| Issue | Scenario Type | Cause | Fix |
|-------|--------------|-------|-----|
| `ORA-00904: invalid identifier` | Function | Parameter not resolved | Check `_normalize()` — abbreviation map missing entry |
| `ORA-00904: "TABLE"` | Both | `TABLE(CAST(...))` detected as missing table | Excluded `TABLE` from dependency check keywords |
| `ORA-00942: table not found` | Non-Function | Missing Oracle table/view | Verify `@MINER@` schema access |
| `ORA-01861: date format` | Both | Date format mismatch | Check batch date format vs Oracle date column |

---

## Database Schema

SQLite at `<APP_BASE_PATH>/db/scenario_debugger.db`

| Table | Purpose |
|-------|---------|
| `users` | Authentication, roles (admin/analyst), bcrypt hashed passwords |
| `jobs` | Every pipeline run: status, scenario name, batch date, root cause, results |
| `job_steps` | Per-step status for each job (powers live progress) |
| `audit_log` | Immutable record of all actions |
