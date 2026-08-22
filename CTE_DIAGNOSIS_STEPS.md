# CTE Diagnosis Steps

## Overview

When a CTE returns 0 rows, the system progressively diagnoses to find the root cause. This document explains each diagnostic step and what verification should happen.

---

## Diagnosis Flow

```
CTE returns 0 rows
        │
        ▼
┌─────────────────────┐
│ Step 1: Dependencies │  → Are all required tables/views present?
└─────────────────────┘
        │ (OK)
        ▼
┌─────────────────────┐
│ Step 2: Full Count   │  → Does the CTE actually return 0?
└─────────────────────┘
        │ (0 rows)
        ▼
┌─────────────────────┐
│ Step 3: JOINs        │  → Does any JOIN eliminate all rows?
└─────────────────────┘
        │ (No)
        ▼
┌─────────────────────┐
│ Step 4: WHERE        │  → Remove each WHERE condition one by one
└─────────────────────┘
        │ (No single killer)
        ▼
┌─────────────────────┐
│ Step 5: HAVING       │  → Remove each HAVING condition one by one
└─────────────────────┘
        │ (No single killer)
        ▼
┌─────────────────────┐
│ Step 6: UNKNOWN      │  → Deep dive into inner subquery
└─────────────────────┘
```

---

## Step Details

### Step 1: Dependencies Check

**Purpose:** Verify all tables/views referenced in the CTE exist in Oracle.

**What happens:**
- Extract all table/view names from FROM and JOIN clauses
- Query Oracle to check if each exists
- If any missing → report and stop

**Output:**
```
[STEP 6] Checking dependencies for Curr_Date_Trxn...
[STEP 6] Missing: SOME_TABLE → FAIL
```

---

### Step 2: Full Count

**Purpose:** Confirm the CTE actually returns 0 rows.

**What happens:**
- Execute `SELECT COUNT(*) FROM (<CTE_SQL>)`
- If count > 0 → CTE is healthy, skip diagnosis
- If count = 0 → proceed to Step 3

**Output:**
```
[STEP 6] EXECUTING SQL: CTE_Curr_Date_Trxn_full_count — 0 rows
[STEP 6] CTE 'Curr_Date_Trxn' returns 0 rows — starting diagnosis...
```

---

### Step 3: JOIN Diagnosis

**Purpose:** Check if any JOIN eliminates all rows.

**What happens:**
- Start with base table (FROM clause)
- Add each JOIN one at a time
- After each JOIN, check row count
- If rows drop from N to 0 at a specific JOIN → that JOIN is the killer

**Verification:** ✅ Should verify by commenting out the JOIN condition in resolved SQL

**Output:**
```
[STEP 6] BASE TABLE ROWS (no joins): 2,427
[STEP 6] JOIN[0]: 2,427 → 0
[STEP 6] JOIN condition kills rows: vca.ACCT_INTRL_ID = ca.ACCT_INTRL_ID
```

---

### Step 4: WHERE Condition Diagnosis

**Purpose:** Find which WHERE condition eliminates all rows.

**What happens:**
- Parse all WHERE conditions (split by AND)
- For each condition, run the query WITHOUT that condition
- If removing condition X restores rows → X is a killer

**Cases:**

| Condition Removal | Rows | Interpretation |
|-------------------|------|----------------|
| Remove cond_0 | 0 | Not a killer |
| Remove cond_1 | 7,281 | ✅ KILLER FOUND |

**Verification:** 
```
[STEP 6] VERIFICATION: Loading resolved SQL from resolved_function_dataset_*.sql
[STEP 6] VERIFICATION: Commenting out: t.BENEF_ACCT_ID = a.ACCT_INTRL_ID
[STEP 6] EXECUTING SQL: verify_killer_commented_out — 7,281 rows
[STEP 6] VERIFICATION RESULT: ✅ ALERTS GENERATED — 7,281 rows returned
```

**Output:**
```
[STEP 6] EXECUTING SQL: where_remove_cond_0 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_1 — 7,281 rows  ← KILLER!
[STEP 6] WHERE condition kills rows: CT.DBT_CDT_CD = 'D'
```

---

### Step 5: HAVING Condition Diagnosis

**Purpose:** Find which HAVING condition eliminates all rows (aggregation filters).

**What happens:**
- Parse all HAVING conditions
- For each condition, run the query WITHOUT that condition
- If removing condition X restores rows → X is a killer

**Verification:** ✅ Should verify by commenting out the HAVING condition in resolved SQL

**Output:**
```
[STEP 6] ROWS BEFORE HAVING filters: 10,000
[STEP 6] EXECUTING SQL: having_add_cond_0 — 0 rows
[STEP 6] EXECUTING SQL: having_add_cond_1 — 0 rows
[STEP 6] EXECUTING SQL: having_add_cond_2 — 500 rows  ← KILLER!
[STEP 6] HAVING condition kills rows: SUM(amt) > 10000
```

---

### Step 6: UNKNOWN — Inner Subquery Analysis

**Purpose:** When no single condition is the killer, analyze the inner structure.

**What happens:**

1. **Check for UNION ALL branches:**
   - Split the inner subquery by UNION ALL
   - Count rows for each branch
   - For zero-count branches, analyze further

2. **Branch Failure Analysis:**
   - For each empty branch, remove WHERE conditions one by one
   - Find the killer condition in that branch

3. **Inner Subquery Without Outer WHERE:**
   ```
   inner_subquery_no_outer_where → 4,854 rows
   ```
   - This means the inner query HAS data
   - But the outer WHERE combination kills ALL rows
   - → This is a "where_combination" issue

---

## Example: Curr_Date_Trxn Diagnosis

### Input CTE
```sql
Curr_Date_Trxn AS (
    SELECT 
        vca.CUST_INTRL_ID,
        SUM(CT.TRXN_AM) AS ONE_DAY_AMT,
        ...
    FROM fccmatomic.CASH_TRXN CT
    JOIN Cust_Accounts vca ON CT.ACCT_INTRL_ID = vca.ACCT_INTRL_ID
    WHERE 
        CT.DBT_CDT_CD = 'D'
        AND CT.MANTAS_TRXN_CHANL_CD = 'ATM'
        AND CT.MANTAS_TRXN_ASSET_CLASS_CD = 'FUNDS'
        AND CT.MANTAS_TRXN_PURP_CD = 'GENERAL'
        AND CT.DATA_DUMP_DT IN (SELECT ...)
        AND CT.TRXN_EXCTN_DT > (SELECT ...)
        AND CT.CXL_PAIR_TRXN_INTRL_ID IS NULL
    GROUP BY vca.CUST_INTRL_ID
)
```

### Diagnosis Log

```
[STEP 6] DIAGNOSING CTE: Curr_Date_Trxn
================================================================================

[STEP 6] Checking dependencies for Curr_Date_Trxn...
[STEP 6] EXECUTING SQL: CTE_Curr_Date_Trxn_full_count — 0 rows
[STEP 6] CTE 'Curr_Date_Trxn' returns 0 rows — starting diagnosis...

[STEP 6] --- Checking JOINs ---
[STEP 6] No JOIN issues found.

[STEP 6] --- Checking WHERE conditions ---
[STEP 6] EXECUTING SQL: where_remove_cond_0 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_1 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_2 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_3 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_4 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_5 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_6 — 0 rows
[STEP 6] EXECUTING SQL: where_remove_cond_7 — 0 rows
[STEP 6] No WHERE issues found.

[STEP 6] --- Checking HAVING conditions ---
[STEP 6] No HAVING issues found.

[STEP 6] --- UNKNOWN: analysing inner subquery ---
[STEP 6] EXECUTING SQL: inner_subquery_no_outer_where — 4,854 rows

====================================================================================================
  ❌ ALERT NOT GENERATING — ROOT CAUSE FOUND
====================================================================================================
  CTE          : Curr_Date_Trxn
  Failure type : WHERE_COMBINATION
  Likely cause : Inner query returns 4,854 rows, but outer WHERE combination eliminates all.

  VERIFICATION REQUIRED:
    - Comment out ALL WHERE conditions in resolved_function_dataset_*.sql
    - Re-execute to check if alerts are generated
```

### Expected Verification (NOT YET IMPLEMENTED)

```
[STEP 6] VERIFICATION: Loading resolved SQL from resolved_function_dataset_20260720_191040.sql
[STEP 6] VERIFICATION: Commenting out WHERE combination (8 conditions)
[STEP 6] EXECUTING SQL: verify_where_combination — 4,854 rows
[STEP 6] VERIFICATION RESULT: ✅ ALERTS GENERATED — 4,854 rows with WHERE conditions commented out
```

---

## Verification Requirements

| Scenario | Verification Required |
|----------|----------------------|
| Single WHERE killer | Comment out that condition in resolved SQL → re-execute → check alerts |
| Single HAVING killer | Comment out that condition in resolved SQL → re-execute → check alerts |
| UNION branch killer | Comment out that condition in resolved SQL → re-execute → check alerts |
| WHERE combination | Comment out ALL WHERE conditions → re-execute → check alerts |

---

## Root Cause Categories

| Type | Description | Example |
|------|-------------|---------|
| `missing_dependencies` | Required table/view doesn't exist | `ORA-00942: table or view does not exist` |
| `execution_error` | SQL syntax or runtime error | Division by zero, invalid column |
| `join_failure` | JOIN condition eliminates all rows | `vca.ACCT_INTRL_ID = ca.ACCT_INTRL_ID` |
| `where_condition` | Single WHERE condition is too restrictive | `CT.DBT_CDT_CD = 'D'` |
| `having_condition` | Single HAVING condition is too restrictive | `SUM(amt) > 10000` |
| `branch_failure` | UNION branch returns 0 rows due to condition | `t.BENEF_ACCT_ID = a.ACCT_INTRL_ID` |
| `where_combination` | No single killer, but combination is too restrictive | All WHERE conditions together kill all rows |
| `no_source_data` | Source tables are empty for batch date | `CASH_TRXN has 0 rows for 2017-01-31` |
| `unknown` | Cannot determine root cause | Complex SQL structure |

---

## Current Gaps (To Be Fixed)

None — all verification gaps have been addressed:

1. ✅ **WHERE Combination Verification**: When `inner_subquery_no_outer_where` returns rows, the system now comments out ALL WHERE conditions in the resolved SQL, re-executes to verify alerts would be generated, and reports this as the root cause. Implemented in `_verify_where_combination()`.

2. ✅ **Branch Failure Verification**: When a killer condition is found in a UNION branch, the system comments out that condition in the resolved SQL, re-executes to verify alerts would be generated. Implemented in `_analyze_branch_failure()`.

---

## Implementation Status

| Step | Implemented | Verification |
|------|-------------|--------------|
| Dependencies | ✅ | N/A |
| Full Count | ✅ | N/A |
| JOINs | ✅ | ❌ Not verified |
| WHERE conditions | ✅ | ✅ Verified |
| HAVING conditions | ✅ | ✅ Verified |
| UNION branch failure | ✅ | ✅ Verified |
| WHERE combination | ✅ Detected | ✅ **VERIFIED** |
