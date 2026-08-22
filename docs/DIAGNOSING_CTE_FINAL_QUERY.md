# DIAGNOSING CTE: final\_query — Complete Flow

When the OFSAA scenario SQL executes successfully (all CTEs return rows) but the final
aggregation produces zero alerts, the pipeline enters Step 6 (SQL Diagnostics) to
diagnose the `final_query`. This document explains exactly how that works.

---

## Entry Point: `_step_sql_diagnostics()` — `backend/pipeline.py`

This is called by the pipeline orchestrator **after** Step 5 (CTE Executer) confirms
all CTEs passed but `final_query` returns 0 rows.

```
Step 5 (CTE Executer)
  ├─ All CTEs returned rows
  └─ final_query returned 0 rows
       └─ Step 6 (_step_sql_diagnostics)
```

### What _step_sql_diagnostics does:

1. **Loads the original dataset query** from `output_dir/extracted_queries/dataset_query_*.sql`
   — this is the full scenario SQL extracted from the log file, stored for later
   re-execution with conditions commented out.

2. **Calls `run_granular_cte_diagnostics()`** on the final query SQL — the core
   diagnostic engine (see below).

3. **After diagnostics completes**, extracts any HAVING killer conditions found
   and attempts to **comment them out in the original dataset query**, then
   **re-executes the FULL scenario SQL** against Oracle to check if alerts
   would generate without those conditions.

4. **Stores the result** in `data_availability.full_scenario_alerts_after_removal`.

---

## `run_granular_cte_diagnostics()` — `pipeline/sql_diagnostics.py`

This is the main diagnostic engine. It runs 7 progressive steps against the
failing CTE/final-query, stopping at the first step that identifies the root cause:

### Step 1: Dependency Check

Checks that all tables and views referenced in the SQL actually exist in the
Oracle schema. If any are missing, reports "missing dependencies" and stops.

```
Query: SELECT view_name FROM user_views UNION ALL SELECT table_name FROM user_tables
       for each referenced object
```

### Step 2: Full Count

Runs `SELECT COUNT(*) FROM (<sql>) t` to confirm the CTE returns 0 rows.
If it returns rows, the CTE is healthy and diagnosis stops.

### Step 3: JOIN Diagnosis — `diagnose_joins()`

Only runs if the SQL has explicit JOIN keywords. Adds joins one at a time,
reporting row counts before and after each join. Stops at the first join that
eliminates all rows.

```
Strategy: start with base FROM table, add each JOIN progressively
Output:  BASE TABLE: 1500 rows
         JOIN[0]: 1500 → 1200  (INNER JOIN table2 ON ...)
         JOIN[1]: 1200 → 0     ❌ first killer join found
```

For the final query, the outer level is typically `FROM (...) t` with no explicit
JOINs, so this step usually finds nothing and proceeds.

### Step 4: WHERE Diagnosis — `diagnose_where_conditions()`

Uses a **"remove one"** strategy: for each top-level WHERE condition, run the
query **without** that condition. If rows return, that condition is a killer.

```
Strategy: remove each condition one at a time, count rows
Output:  WHERE remove[0]: rows_without = 350  |  max(t.date) <= SYSDATE
         WHERE remove[1]: rows_without = 0    |  sum(amt) >= 1000
         → condition[1] is the killer
```

Also performs:
- **OR-block drill-down**: if the killer is an OR block, split by OR and test
  each branch individually
- **Source table verification**: runs the condition directly against the source
  table to check data availability
- **Threshold suggestions**: queries actual column distribution (MIN, MAX, P25,
  P75) and suggests adjusted values

For the final query, there is typically no outer WHERE (conditions are inside
the UNION ALL branches), so this step usually finds nothing.

### Step 5: UNION ALL Branch Probing — `_probe_union_all_branches()`

Always runs regardless of previous steps. Splits the SQL by UNION ALL, counts
rows per branch individually, and reports which branches contribute data and
which are empty.

```
Output:  Branch  1  ✅   1,234 rows  FROM wires
         Branch  2  ✅     856 rows  FROM mitrxn
         Branch  3  ❌       0 rows  FROM fccmatomic.CASH_TRXN
                               → Source table(s) empty: fccmatomic.CASH_TRXN
         Branch  4  ❌       0 rows  FROM fccmatomic.BACK_OFFICE_TRXN
                               → Source table(s) empty: fccmatomic.BACK_OFFICE_TRXN
```

**Important**: empty branches do NOT block diagnosis. As long as **at least 1
branch** returns rows, the pipeline moves forward. Empty branches are
informational only.

### Step 6: HAVING Diagnosis — `diagnose_having_conditions()`

This is the most critical step for `final_query`. The `parse_sql_structure()`
function extracts the `before_having` part (everything before the HAVING clause)
and the individual HAVING conditions.

```
Strategy: add conditions one-by-one, report rows before → after each
Output:  ROWS BEFORE HAVING: 35
         HAVING[0]: 35 → 35  ✅  max(t.data_dump_dt) <= (select ...)
         HAVING[1]: 35 → 12  ⚠️  count(distinct acct) >= 2   (-23 rows)
         HAVING[2]: 12 → 0   ❌  sum(t.Trxn_Am) >= 1000
```

For each condition:

| Condition effect | Icon | Color | Meaning |
|---|---|---|---|
| `prev_count > 0 and cur == 0` | ❌ | Red | Kills all rows → killer |
| `prev_count > 0 and cur < prev_count` | ⚠️ | Orange | Reduces rows but doesn't eliminate |
| `prev_count == cur` | ✅ | Green | Passes through unchanged |

Killer and reducer conditions also get:
- **Threshold suggestions**: actual column distribution stats + simulated lower
  values that would restore rows
- **Line numbers**: tracked from the original SQL for UI highlighting

### Step 7: UNKNOWN — Inner Subquery Analysis — `_analyze_inner_subquery()`

Only reached if JOINs, WHERE, and HAVING all found nothing, but the CTE still
returns 0 rows. This is the deepest level of analysis.

#### Strategy 1: Outer WHERE check

Strips the outer WHERE clause and counts rows. If rows return, the outer WHERE
combination is too restrictive.

#### Strategy 2: Inner HAVING elimination

Strips the inner HAVING clause. If rows return, performs a **progressive
elimination drill** that goes beyond the basic HAVING diagnosis:

1. Starts with the full SQL (all HAVING conditions present)
2. Adds conditions one-by-one (same as Step 6)
3. When a killer is found, **comments it out** with `-- ` in the working SQL
4. Re-counts with that condition removed
5. **Continues** to check remaining conditions (does NOT stop at first killer)
6. Accumulates all killers into a list

```python
for condition in having_conditions:
    count = execute_count(working_sql_with_condition)
    if kills_rows:
        modified_sql = comment_out(working_sql, condition)
        restored_count = execute_count(modified_sql)
        working_sql = modified_sql  # keep it commented for next iteration
```

After all conditions processed:
- If rows are restored → report which conditions were the killers
- If still 0 rows → flag for **deeper analysis**

#### Strategy 3: Deep Drill — `_drill_deeper()`

When HAVING killers don't explain all row loss, drills into:

| Level | What it checks | How |
|---|---|---|
| **Level 2** | WHERE conditions | Remove one-by-one, find killer |
| **Level 3** | Source tables + UNION ALL | Probe row counts per table, per branch |
| **Level 4** | Calendar date | Check business date exists in KDD_CAL |

This data is saved to the **run log file** only (not shown in UI).

---

## Full Scenario Re-execution

After diagnostics completes, `_step_sql_diagnostics()` performs one final check:

1. **Load the original dataset query** — the full scenario SQL extracted from the
   OFSAA log file by Step 1 (Log Reader)

2. **For each HAVING killer** found during diagnosis:
   - Try to match the condition text in the original SQL (exact match first,
     then normalized whitespace match)
   - Comment it out with `-- ` in the original SQL

3. **Re-execute the modified full scenario** against Oracle using
   `execute_dataset_query()`

4. **Return the result**:

   | Result | Meaning | UI shows |
   |---|---|---|
   | `true` | Removing the killer conditions allows alerts to generate | ✅ Green box |
   | `false` | Even without those conditions, the scenario still produces no alerts | ❌ Red box — deeper issue |

   The result is also appended to the root cause text and saved to the run log.

---

## Run Log

Every pipeline execution creates a detailed log file at:

```
outputs/<scenario_name>/run_<scenario>_<job_id_short>.log
```

The log captures everything:

```
==========================================================================================
  OFSAA SCENARIO DEBUGGER — RUN LOG
  Job ID     : abc12345
  Scenario   : ML-HRTransFocalHRE
  Executed   : 2026-07-14 19:46:23
==========================================================================================

----------------------------------------------------------------------
  Step log_reader — starting
----------------------------------------------------------------------
[19:46:23] [LOG_READER] Completed — Scenario: ML-HRTransFocalHRE | Queries: 3

----------------------------------------------------------------------
  Step sql_executer — starting
----------------------------------------------------------------------
[19:46:23] [SQL_EXECUTER] Completed — No alerts generated

...

----------------------------------------------------------------------
  CTE Waterfall Results
----------------------------------------------------------------------
[19:46:23] [CTE] wires: 1,500 rows  ✅
[19:46:23] [CTE] mitrxn: 856 rows  ✅
[19:46:23] [CTE] Cust_Accounts: 1,234 rows  ✅

[DIAG] HAVING drill:
[DIAG]   ⚠️ count(distinct acct) >= 2  |  35 → 12 (-23 rows)  |  Line 42
[DIAG]   ❌ sum(t.Trxn_Am) >= 1000      |  12 → 0              |  Line 48

----------------------------------------------------------------------
  Full Scenario Re-execution
----------------------------------------------------------------------
[19:46:23] [RE-EXEC] ✅ After removing killer HAVING conditions, the FULL scenario generates alerts!

----------------------------------------------------------------------
  Root Cause
----------------------------------------------------------------------
[19:46:23] [RESULT] CTE 'final_query' returns 0 rows. HAVING condition eliminates all rows.
```

---

## UI Display Overview

The Results page shows the diagnostics in a layered structure:

```
Root Cause Analysis Card
├── Failure type: HAVING / WHERE / UNKNOWN
├── Likely cause (text)
├── Failing condition (SQL snippet)
│
├── Inner HAVING Conditions        ← progressive drill
│   ├── ✅ 35 → 35   cond_1        ← passes
│   ├── ⚠️ 35 → 12   cond_2        ← reduces rows (-23)
│   │   └── Line 42
│   └── ❌ 12 → 0    cond_3        ← kills rows
│       ├── Line 48
│       ├── Likely cause
│       └── Threshold suggestions
│
├── Full Scenario Re-execution     ← after commenting killers
│   └── ✅ / ❌  result
│
├── SQL with highlighted lines     ← full SQL, killer lines in red/orange
│
└── Threshold Suggestions          ← data distribution + simulated values
    ├── Column: sum(amt)
    │   ├── Current: ≥ 1000
    │   ├── Actual data: 45 → 850
    │   └── Suggested: ≥ 500 (restores 12 rows)
    └── ...
```
