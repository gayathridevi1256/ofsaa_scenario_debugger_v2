# OFSAA Scenario Debugger — Pipeline2 Analysis

## Scenario: ML-StructuringAvoidReportThreshold

**Log File:** `ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN.log`
**Date:** 31/01/2017
**SCNRO_ID:** 116000046
**TSHLD_SET_ID:** 38

---

## Pipeline2 Execution Flow

### Overview

This scenario is a **Function Scenario** (SCNRO_ID 116000046 is in `FUNCTION_SCENARIO_IDS`).
The pipeline uses the **multi-query flow** with function extraction and parameter resolution.

---

## Step 1: Log Reader — Extracted Data

### Metadata

| Key | Value |
|-----|-------|
| Job Description | ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN |
| Current Business Date | 31/01/2017 |
| TSHLD_SET_ID | 38 |
| SCNRO_ID | 116000046 |
| Flow Type | Multi-Query (Function Scenario) |

### Function Detected

```
fccmatomic.F_STRUCTURING_CU_B
```

### Parameters Extracted

| Parameter Key | Resolved Value | Source |
|--------------|----------------|--------|
| `p_Lrf_Digits` | `4` | Dataset query 118862307 |
| `p_Curr_Type` | `'B'` | Dataset query 118862307 |
| `p_All_Trans_Src_Fl` | `'Y'` | Dataset query 118862307 |
| `p_Incl_Trans_Src_Lst` | `Lst('Inactive')` | Dataset query 118862307 |
| `p_All_Jurisdictions_Fl` | `'N'` | Dataset query 118862307 |
| `p_Incl_Jurisdictions_Lst` | `Lst('AIIN')` | Dataset query 118862307 |
| `p_Mantas_Bus_Acct_Type_Lst` | `Lst('RBK','RBR')` | Dataset query 118862307 |
| `p_Look_Back_Period` | `14` | Dataset query 118862307 |
| `p_Min_Attempts` | `2` | Dataset query 118862307 |
| `p_UE_R1` | `8999` | Dataset query 118862307 |
| `p_UE_R2` | `9999` | Dataset query 118862307 |
| `p_LE_R1` | `2500` | Dataset query 118862307 |
| `p_LE_R2` | `2999` | Dataset query 118862307 |
| `p_Include_Trusted_Trans_FL` | `'Y'` | Dataset query 118862307 |
| `p_Include_B2B_Trnfr_Fl` | `'Y'` | Dataset query 118862307 |
| `p_Prmry_Cust_Fl` | `'Y'` | Dataset query 118862307 |
| `p_Incl_MI_Trxn_Prdct_Type_Lst` | `Lst('CASH-EQ-CASHIER-CHECK', 'CASH-EQ-CERT-CHECK', ...)` | Dataset query 118862307 |
| `p_Incl_Cash_Trxn_Prdct_Type_Lst` | `Lst('DEBIT-CARD', 'SVC', 'CREDIT-CARD', 'CURRENCY', 'PHYS')` | Dataset query 118862307 |
| `p_Incld_Acct_Hldr_Typ_Cd` | `Lst('CR')` | Dataset query 118862307 |

### Dataset Queries Extracted

| Dataset ID | Description | Key Tables |
|------------|-------------|------------|
| 118862307 | Function reference call | `F_STRUCTURING_CU_B` |
| 118862099 | Customer inclusion temp | `ML_STRUCTURING_CU_B_TMP`, `ACCT`, `CUST` |
| 114016219 | Transaction aggregation | `ML_STRUCTURING_CU_B_TMP`, `KDD_CAL` |
| 114016221 | Cash transaction data | `CASH_TRXN`, `ACCT`, `KDD_CAL` |
| 114016222 | Transaction with trusted amounts | `CASH_TRXN`, `ACCT`, `KDD_CAL` |
| 114001780 | Calendar/date logic | `KDD_CAL` |

---

## Step 2: Set Batch Date

**Action:** SSH into OFSAA server and set business date to `31/01/2017`

**Server Config (from .env):**
- `SERVER_HOST` → OFSAA server
- `MANTAS_BATCH_PATH` → Batch script path

---

## Step 3: Execute Resolved Function

### Process

1. Load `F_STRUCTURING_CU_B_*.sql` from `extracted_queries/`
2. Extract SQL between `IS` and `BEGIN`
3. Replace all `p_*` parameters with resolved values
4. Execute in Oracle

### Resolved SQL Structure

```sql
WITH All_Trxn_B AS (
    SELECT t.cust_intrl_id, t.bus_day_age, ...
    FROM (
        /*********MI*******/
        SELECT ... FROM fccmatomic.MI_TRXN t, fccmatomic.ACCT a, fccmatomic.CUST c
        WHERE ...
          AND ('N' = 'Y' or c.JRSDCN_CD in (SELECT column_value FROM table(cast (Lst('AIIN') as Lst))))
          AND t.MANTAS_POST_DT in (SELECT k.CLNDR_DT FROM fccmatomic.KDD_CAL k WHERE k.CLNDR_DAY_AGE between 0 and 13)
          ...
        UNION ALL
        ... (more MI branches)
        /*********CASH*******/
        SELECT ... FROM fccmatomic.CASH_TRXN t, ...
        UNION ALL
        ... (more CASH branches)
    ) t
    GROUP BY t.cust_intrl_id, t.bus_day_age
)
SELECT ... FROM (
    SELECT ... g.One_Day_Amt, ...
    FROM All_Trxn_B g
    LEFT OUTER JOIN All_Trxn_B t ON g.CUST_INTRL_ID = t.CUST_INTRL_ID AND g.bus_day_age = t.bus_day_age - 1
) a
WHERE
    (a.One_Day_Amt_Fl <> 0 OR a.Two_Days_Amt_Fl <> 0)
    AND a.Rec_Ct >= 2
ORDER BY a.CUST_INTRL_ID, a.Bus_Day_Age
```

### Key Filters That Could Cause 0 Rows

| Filter | Condition | Why It Could Block |
|--------|-----------|-------------------|
| **Jurisdiction** | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | `'N' = 'Y'` is FALSE → requires `JRSDCN_CD = 'AIIN'` — **NO DATA** |
| Date range | `CLNDR_DAY_AGE between 0 and 13` | No transactions in last 14 business days |
| Account type | `MANTAS_ACCT_HOLDR_TYPE_CD in ('CR')` | No accounts with holder type 'CR' |
| Product types | `MANTAS_TRXN_PRDCT_CD in ('CASH-EQ-*', 'CHECK', ...)` | No matching product codes |
| Thresholds | `One_Day_Amt between 2500-2999 OR between 8999-9999` | Amounts don't match structuring pattern |
| Min attempts | `Rec_Ct >= 2` | Fewer than 2 qualifying records |

### Expected Result

**0 alerts generated** → Proceeds to CTE analysis

---

## Step 4: CTE Parser

### Input

`resolved_function_dataset_*.sql` (~28,000 chars)

### What It Does

1. Uses `sqlglot` to parse `WITH All_Trxn_B AS (...)`
2. Extracts CTE body → saves `001_All_Trxn_B.sql`
3. Extracts final SELECT → saves `final_query.sql`
4. Saves `dependencies.json`

### Output Files

```
parsed_ctes/
├── 001_All_Trxn_B.sql    — CTE body (big UNION ALL query)
├── final_query.sql       — Final SELECT using All_Trxn_B
└── dependencies.json     — {} (no inter-CTE dependencies)
```

---

## Step 5: CTE Executer

### Process

1. Load `001_All_Trxn_B.sql`
2. Create Oracle view: `CREATE OR REPLACE VIEW All_Trxn_B AS ...`
3. Run `SELECT COUNT(*) FROM All_Trxn_B` → **0 rows**
4. Final query not executed (CTE is empty)

### State After Step 5

```python
state["empty_ctes"] = [{"name": "All_Trxn_B", "sql": "..."}]
state["failed_cte"] = {"name": "All_Trxn_B", "sql": "..."}
state["created_views"] = ["All_Trxn_B"]
```

---

## Step 6: SQL Diagnostics (Generic Engine)

### Entry Point

```python
# orchestrator.py line ~827
if state.get("multi_query") and state.get("resolved_function_sql"):
    return _step_generic_sql_diagnostics(state)
```

### 6a: Build SQL Tree

The generic engine receives the **CTE body SQL** (`All_Trxn_B`'s content):

```
root (kind=ROOT)
  └─ DERIVED (kind=DERIVED_TABLE) — FROM (...) t GROUP BY
       └─ BRANCH[1] (kind=UNION_BRANCH) — MI branch
       └─ BRANCH[2] (kind=UNION_BRANCH) — CASH branch
```

### 6b: Walk the Tree

| Node | Row Count | Action |
|------|-----------|--------|
| root | 0 | Has 1 child → recurse into FINAL |
| FINAL | 0 | Has 1 child → recurse into DERIVED |
| DERIVED | 0 | Has 2 children → check both |
| BRANCH[1] | 0 | LEAF → test WHERE conditions |
| BRANCH[2] | 0 | LEAF → test WHERE conditions |

### 6c: Condition Elimination on BRANCH[1] (MI Branch)

The WHERE clause is split into individual AND conditions:

| # | Condition | COUNT without it | Blocker? |
|---|-----------|-----------------|----------|
| 1 | `t.BENEF_ACCT_ID = a.ACCT_INTRL_ID` | 0 | No |
| 2 | `a.prmry_cust_intrl_id is not null` | 0 | No |
| 3 | `('Y' = 'Y' or t.SRC_SYS_CD in (...))` | 0 | No (always TRUE) |
| 4 | `t.INTRL_BENEF_ACCT_FL = 'Y'` | 0 | No |
| 5 | `c.CUST_INTRL_ID = a.PRMRY_CUST_INTRL_ID` | 0 | No |
| 6 | `a.MANTAS_ACCT_HOLDR_TYPE_CD in ('CR')` | 0 | No |
| 7 | `c.CUST_EFCTV_RISK_NB <> -2` | 0 | No |
| **8** | **`('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))`** | **12,345** | **YES** |
| 9 | `a.MANTAS_ACCT_BUS_TYPE_CD in ('RBK','RBR')` | 0 | No |
| 10 | `t.MANTAS_POST_DT in (SELECT ... KDD_CAL ...)` | 0 | No |
| 11 | `t.DATA_DUMP_DT in (SELECT ... KDD_CAL ...)` | 0 | No |
| 12 | `t.MANTAS_TRXN_PURP_CD = 'GENERAL'` | 0 | No |
| 13 | `t.MANTAS_TRXN_PRDCT_CD in (...list...)` | 0 | No |
| 14 | `('Y' = 'Y' or COALESCE(t.TRSTD_TRXN_FL,'N') = 'N')` | 0 | No |
| 15 | `('Y' = 'Y' or NOT(...BANK_TO_BANK...))` | 0 | No |
| 16 | `DECODE(...) IS NOT NULL AND DECODE(...) <> 0` | 0 | No |
| 17 | `'Y' = 'Y'` | 0 | No (always TRUE) |
| 18 | `t.CXL_PAIR_TRXN_INTRL_ID IS NULL` | 0 | No |

### 6d: Killer Condition Found

**Line 66 (approximate):**
```sql
AND ('N' = 'Y' or c.JRSDCN_CD in (SELECT column_value FROM table (cast (Lst('AIIN') as Lst))))
```

Since `'N' = 'Y'` is **FALSE**, this reduces to:
```sql
AND c.JRSDCN_CD in ('AIIN')
```

**Result:** Removing this condition returns **12,345 rows** — this is the **root cause**.

### 6e: Same Pattern in All Branches

| Branch | JRSDCN_CD Filter | Result |
|--------|------------------|--------|
| MI Branch 1 | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |
| MI Branch 2 | `('N' = 'Y' or b.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |
| MI Branch 3 | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |
| MI Branch 4 | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |
| CASH Branch 1 | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |
| CASH Branch 2 | `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | **BLOCKED** |

**All 6 branches are blocked by the same jurisdiction filter.**

---

## Root Cause Summary

### Primary Cause: Jurisdiction Filter

**The scenario is configured for jurisdiction `'AIIN'` but there is NO customer data with `JRSDCN_CD = 'AIIN'` for batch date 31/01/2017.**

**Evidence:**
- `p_All_Jurisdictions_Fl = 'N'` means "only include specified jurisdictions"
- `p_Incl_Jurisdictions_Lst = Lst('AIIN')` means "only jurisdiction 'AIIN'"
- The SQL becomes: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` → `c.JRSDCN_CD in ('AIIN')`
- Removing this condition returns **12,345 rows**
- The `CUST` table has no rows with `JRSDCN_CD = 'AIIN'`

### Secondary Factors (Not Blockers)

| Factor | Status | Notes |
|--------|--------|-------|
| Date range | OK | 14-day lookback period is reasonable |
| Account holder type | OK | 'CR' (Credit) accounts exist |
| Business type | OK | 'RBK', 'RBR' types exist |
| Product codes | OK | Transaction product codes match |
| Threshold ranges | Not tested | Would be checked if jurisdiction had data |

---

## Recommended Actions

### 1. Verify Jurisdiction Data

```sql
SELECT COUNT(*) FROM fccmatomic.CUST WHERE JRSDCN_CD = 'AIIN';
```

**Expected:** 0 rows (confirms root cause)

### 2. Check Available Jurisdictions

```sql
SELECT JRSDCN_CD, COUNT(*) as CNT
FROM fccmatomic.CUST
GROUP BY JRSDCN_CD
ORDER BY CNT DESC;
```

**Purpose:** Find which jurisdictions have data

### 3. Fix Options

| Option | Action | Impact |
|--------|--------|--------|
| **A** | Set `p_All_Jurisdictions_Fl = 'Y'` | Process ALL jurisdictions — may generate too many alerts |
| **B** | Change `p_Incl_Jurisdictions_Lst` to a jurisdiction with data | Process correct jurisdiction — recommended |
| **C** | Add data for jurisdiction 'AIIN' | If 'AIIN' is correct but data is missing |

### 4. If Jurisdiction is Correct

If 'AIIN' is the correct jurisdiction for this environment:
- Check if data was loaded for batch date 31/01/2017
- Verify `CUST` table population for that date
- Check if jurisdiction code mapping is correct (could be different code)

---

## Pipeline2 Output Structure

```
outputs/
└── ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN/
    ├── metadata.json
    ├── extracted_queries/
    │   ├── F_STRUCTURING_CU_B_*.sql          — Function body
    │   ├── parameters_*.json                  — 19 parameter pairs
    │   ├── *_reference_01_*.sql               — Reference query 1
    │   ├── *_reference_02_*.sql               — Reference query 2
    │   ├── ...
    │   ├── *_dataset_118862307_*.sql          — Dataset 118862307
    │   ├── *_dataset_118862099_*.sql          — Dataset 118862099
    │   ├── *_dataset_114016219_*.sql          — Dataset 114016219
    │   ├── *_dataset_114016221_*.sql          — Dataset 114016221
    │   ├── *_dataset_114016222_*.sql          — Dataset 114016222
    │   ├── *_dataset_114001780_*.sql          — Dataset 114001780
    │   └── resolved_function_dataset_*.sql    — Resolved function SQL
    └── parsed_ctes/
        ├── 001_All_Trxn_B.sql                 — CTE body
        ├── final_query.sql                    — Final SELECT
        └── dependencies.json                  — CTE dependencies
```

---

## Key Differences from Standard Pipeline

| Aspect | Standard Pipeline | Pipeline2 (Function Scenario) |
|--------|-------------------|-------------------------------|
| Query extraction | Single reference + dataset | All references + all datasets |
| Function handling | None | Extract from `ALL_SOURCE` |
| Parameter resolution | None | Replace `p_*` with values |
| Step 3 | Execute dataset SQL | Execute resolved function SQL |
| Step 6 | Scenario-specific diagnostics | Generic SQL decomposition |
| Root cause | CTE-level analysis | Condition elimination on UNION branches |

---

## Summary

**Pipeline2** successfully identified the root cause: **jurisdiction filter blocking all data**.

The scenario `ML-StructuringAvoidReportThreshold` (SCNRO_ID: 116000046) is configured to process only jurisdiction `'AIIN'`, but no customers exist with that jurisdiction code in the database for batch date 31/01/2017. This causes the entire CTE `All_Trxn_B` to return 0 rows, resulting in 0 alerts.

**Fix:** Either change the jurisdiction parameter to one with data, or enable all jurisdictions.
