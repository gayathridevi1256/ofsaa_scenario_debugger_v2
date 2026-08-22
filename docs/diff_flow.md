# Flow Comparison: Old vs New

## Overview
| Aspect | Old Flow | New Flow |
|--------|----------|----------|
| Reference Queries | Extracts **first** "After expansion" SQL only | Extracts **ALL** "After expansion" SQL blocks |
| Dataset Queries | Extracts **first** "Preparing to select from dataset" SQL only | Extracts **ALL** dataset queries with their IDs |
| Function Extraction | Not supported | Extracts Oracle functions from first queries, saves bodies |
| SCNRO_ID | Not extracted | Extracted from log and saved to metadata.json |

---

## File Output Comparison

### Old Flow Output
```
extracted_queries/
  scenario_main_query_20260716_123456.sql      ← First reference query only
  scenario_dataset_query_20260716_123456.sql   ← First dataset query only
metadata.json
  {
    "job_description": "...",
    "current_business_date": "...",
    "tshld_set_id": "..."
  }
```

### New Flow Output
```
extracted_queries/
  scenario_reference_01_20260716_123456.sql    ← All reference queries numbered
  scenario_reference_02_20260716_123456.sql
  ...
  scenario_reference_N_20260716_123456.sql
  scenario_dataset_118862307_20260716_123456.sql   ← All dataset queries with IDs
  scenario_dataset_118862099_20260716_123456.sql
  ...
  scenario_dataset_<ID>_20260716_123456.sql
  F_STRUCTURING_CU_R_20260716_123456.sql          ← Function bodies (if present)
  ANOMATMEXCESS_PIPELINE_20260716_123456.sql
metadata.json
  {
    "job_description": "...",
    "current_business_date": "...",
    "tshld_set_id": "...",
    "scnro_id": "116000046"                     ← New field
  }
```

---

## Function Extraction Flow

| Step | Old Flow | New Flow |
|------|----------|----------|
| Detection | N/A | Scans first reference + first dataset query for `@MINER@.F_*` patterns |
| Extraction | N/A | Queries `ALL_SOURCE` from Oracle for each function |
| Body Parsing | N/A | Extracts SQL between `IS/AS` and `;` |
| Saving | N/A | Saves each as `functionname_timestamp.sql` |

---

## Backward Compatibility

| Component | Status | Notes |
|-----------|--------|-------|
| `get_latest_dataset_query_file()` | Updated | Changed pattern from `"dataset_query"` to `"dataset_"` |
| `get_latest_dataset_query()` | Updated | Changed pattern from `"dataset_query"` to `"dataset_"` |
| Existing code reading `dataset_query.sql` | ⚠️ Breaking | Old file name no longer created; use `"dataset_" in filename` pattern |

---

## Log Reader Changes

| Function | Old Behavior | New Behavior |
|----------|--------------|--------------|
| `_extract_all_queries_from_log()` | Returns `{"main": str, "dataset": str}` | Returns `{"main_queries": [], "dataset_queries": [], "main": str, "dataset": str}` |
| `_save_queries_to_files()` | Saves 2 files | Saves N reference + N dataset + M function files |
| `_save_metadata()` | Saves 3 fields | Saves 4 fields (added `scnro_id`) |

---

## Step-by-Step Flow

### Old Flow
1. Parse log → Extract **first** reference query
2. Parse log → Extract **first** dataset query
3. Save `main_query.sql` and `dataset_query.sql`
4. Save `metadata.json` with 3 fields

### New Flow
1. Parse log → Extract **ALL** reference queries (numbered 01, 02, ...)
2. Parse log → Extract **ALL** dataset queries (with dataset IDs)
3. Save reference files, dataset files
4. Extract function names from first queries
5. Connect to Oracle → Query function bodies
6. Save function files
7. Extract `SCNRO_ID` from log
8. Save `metadata.json` with 4 fields

---

## Impact on Downstream Steps

| Step | Impact | Notes |
|------|--------|-------|
| Step 3: SQL Executer | ✅ Compatible | Uses `get_latest_dataset_query_file()` which now matches `dataset_*.sql` |
| Step 4: CTE Parser | ✅ Compatible | Uses `get_latest_dataset_query()` which now matches `dataset_*.sql` |
| Step 5: CTE Executer | ✅ Compatible | Reads CTE files, unaffected |
| Step 6: SQL Diagnostics | ✅ Compatible | Uses dataset query, unaffected |
