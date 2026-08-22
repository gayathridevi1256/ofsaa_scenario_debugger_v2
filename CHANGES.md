# Changes Summary

## 1. Parameter Resolution Fix (`orchestrator.py`)
**Problem:** JSON keys didn't match SQL param names (e.g., `p_Incl_MI_Trxn_Prdct_Type_Lst` → `p_Incl_MI_Prdct_Type_Lst`)

**Solution:** Self-discovering parameter matcher
- Extracts SQL params from function signature
- Normalizes both sides (expand abbreviations, strip filler words, remove underscores)
- Matches by exact normalized name first, Levenshtein similarity fallback (>0.6)
- Replaces matched SQL param with JSON value

## 2. GROUP BY Fix (`sql_diagnostics.py`)
**Problem:** `parse_sql_structure()` was extracting SELECT + FROM + JOINs but **skipping GROUP BY**

**Solution:** Added `from_with_joins_and_group` field that includes GROUP BY clause
- All diagnosis functions now use `from_with_joins_and_group` instead of `from_with_joins`
- Ensures aggregated queries are diagnosed correctly

## 3. Step 6 Comprehensive Logging (`sql_diagnostics.py`)
**Problem:** Diagnostics ran but results weren't logged to run log file

**Solution:** Added logging for every SQL executed and every result:
- `execute_count()` — logs every SQL + row count
- `execute_full_sql()` — new function for full SQL execution (not wrapped in COUNT)
- Threshold params loaded
- CTE diagnosis start/end
- JOIN analysis results
- WHERE analysis results (each condition + rows before/after)
- HAVING analysis results
- Inner subquery results
- UNION branch counts
- Final root cause summary

## 4. Verification Step (`sql_diagnostics.py`)
**Problem:** When a killer condition was found (e.g., removing it restores 7,281 rows), the system only reported it — didn't verify by re-executing the full SQL.

**Solution:** Added `verify_killer_condition()` function that:
- Loads the resolved function SQL file
- Comments out the killer condition
- Re-executes the full SQL
- Checks if alerts are generated
- Reports result: ✅ ALERTS GENERATED or ❌ Still NO ALERTS

**Integrated into:**
- `diagnose_where_conditions()` — verifies WHERE killers
- `diagnose_having_conditions()` — verifies HAVING killers
- `_analyze_branch_failure()` — verifies UNION branch condition killers

**Log output example:**
```
[STEP 6] VERIFICATION: Loading resolved SQL from resolved_function_dataset_20260720_172733.sql
[STEP 6] VERIFICATION: Commenting out: t.MANTAS_TRXN_PRDCT_CD IN (...)
[STEP 6] EXECUTING SQL: verify_killer_commented_out — 7,281 rows
[STEP 6] VERIFICATION RESULT: ✅ ALERTS GENERATED — 7,281 rows returned with condition commented out
```

## 5. Dependency Check Fix (`sql_diagnostics.py`)
**Problem:** `check_dependency_views_exist()` was detecting Oracle's `TABLE(CAST(...))` syntax as a missing table called "TABLE"

**Solution:** Added SQL keywords exclusion list (`TABLE`, `SELECT`, `CAST`, `LST`, etc.) to skip false positives.

## 6. Cache Removed (`job_service.py`)
**Problem:** Pipeline was returning cached results instead of running fresh

**Solution:** Removed cache check — every run now always creates a new job and executes the pipeline.

## 7. Routing Fix (`jobs.py`)
**Problem:** `/api/jobs` was returning 307 redirect (losing auth header)

**Solution:** Changed `@router.get("/")` to `@router.get("")` so endpoint responds directly to `/api/jobs` without redirect.

## 8. Async Fix (`job_service.py`, `jobs.py`)
**Problem:** `asyncio.create_task()` was called from sync function — pipeline never started

**Solution:** Made `run_pipeline_job()`, `run_batch_jobs()`, `rerun_job()` async, and updated routes to `await` them.

## 9. Queue Fix (`job_service.py`)
**Problem:** Pipeline was receiving `ConnectionManager` instead of `asyncio.Queue` — `_emit(queue.put())` failed

**Solution:** Created proper `asyncio.Queue`, added `_forward_queue_to_ws()` to forward queue messages to WebSocket.
