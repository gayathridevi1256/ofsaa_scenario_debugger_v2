# OFSAA Scenario Debugger — Pipeline Analysis for ML-StructuringAvoidReportThreshold

## Log File
```
D:\One Drive\OneDrive - JMR Infotech India (P) Ltd\D Drive Data\hari\project\Scenario_Debugger\ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN.log
```

---

## Step 1: Log Reader — What It Extracts

### Metadata Found
```
Job description: ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN
Current business date: 31/01/2017
TSHLD_SET_ID: 38
SCNRO_ID: 116000046
```

### Flow Detection
```python
SCNRO_ID 116000046 is in FUNCTION_SCENARIO_IDS
→ multi_query = True  (function scenario)
```

### Queries Extracted
| Type | Count | Details |
|------|-------|---------|
| Reference | 6 | SQL queries logged as "After expansion, sql = ..." |
| Dataset | 6 | IDs: 118862307, 118862099, 114016219, 114016221, 114016222, 114001780 |

### Function Extraction
From the log, the function name is found:
```
fccmatomic.F_STRUCTURING_CU_B
```

The function body is extracted from Oracle's `ALL_SOURCE` table via:
```sql
SELECT TEXT FROM ALL_SOURCE WHERE NAME = 'F_STRUCTURING_CU_B' AND TYPE = 'FUNCTION' ORDER BY LINE
```

### Parameter Extraction
From reference query 1 and dataset query 1:
```
Reference: fccmatomic.F_STRUCTURING_CU_B(@p_Lrf_Digits, @p_Curr_Type, @p_All_Trans_Src_Fl, ...)
Dataset:   fccmatomic.F_STRUCTURING_CU_B(4, 'B', 'Y', Lst('Inactive'), ...)
```

Matched parameters:
| Parameter Key | Resolved Value |
|--------------|----------------|
| `p_Lrf_Digits` | `4` |
| `p_Curr_Type` | `'B'` |
| `p_All_Trans_Src_Fl` | `'Y'` |
| `p_Incl_Trans_Src_Lst` | `Lst('Inactive')` |
| `p_All_Jurisdictions_Fl` | `'N'` |
| `p_Incl_Jurisdictions_Lst` | `Lst('AIIN')` |
| `p_Mantas_Bus_Acct_Type_Lst` | `Lst('RBK','RBR')` |
| `p_Look_Back_Period` | `14` |
| `p_Min_Attempts` | `2` |

**Files saved to `extracted_queries/`:**
- `F_STRUCTURING_CU_B_*.sql` — function body (IS ... BEGIN)
- `parameters_*.json` — 9 key-value pairs
- 6 reference query files
- 6 dataset query files

---

## Step 2: Set Batch Date
SSH into OFSAA server and set business date to `31/01/2017`.

---

## Step 3: Execute Resolved Function

### What Happens
1. Load `F_STRUCTURING_CU_B_*.sql`
2. Extract SQL between `IS` and `BEGIN`
3. Replace all `p_*` parameters with values from `parameters.json`:
   ```
   p_Lrf_Digits → 4
   p_Curr_Type → 'B'
   p_All_Jurisdictions_Fl → 'N'
   p_Incl_Jurisdictions_Lst → Lst('AIIN')
   ...
   ```
4. Save as `resolved_function_dataset_*.sql`
5. Execute in Oracle → **0 alerts generated**

### Resolved SQL Structure
The resolved SQL looks like:
```sql
WITH All_Trxn_B AS (
    SELECT t.cust_intrl_id, t.bus_day_age, ...
    FROM (
        /*********MI*******/
        SELECT ... FROM fccmatomic.MI_TRXN t, fccmatomic.ACCT a, fccmatomic.CUST c
        WHERE ...
          AND ('N' = 'Y' or c.JRSDCN_CD in (SELECT column_value FROM table(cast (Lst('AIIN') as Lst))))
          AND t.MANTAS_POST_DT in (SELECT k.CLNDR_DT FROM fccmatomic.KDD_CAL k WHERE k.CLNDR_DAY_AGE between 0 and 14-1)
          ...
        UNION ALL
        ... (more branches)
        /*********CASH*******/
        SELECT ... FROM fccmatomic.CASH_TRXN t, ...
        UNION ALL
        ...
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

**Key filters that could cause 0 rows:**

| Filter | Line | Condition | Why It Could Block |
|--------|------|-----------|-------------------|
| Jurisdiction | ~66 | `('N' = 'Y' or c.JRSDCN_CD in (...'AIIN'...))` | `'N' = 'Y'` is FALSE, so it requires `JRSDCN_CD = 'AIIN'` — if no customers have this jurisdiction, 0 rows |
| Date range | ~70-71 | `t.MANTAS_POST_DT in (SELECT k.CLNDR_DT FROM KDD_CAL WHERE CLNDR_DAY_AGE between 0 and 13)` | No transactions in the last 14 business days |
| Account type | ~64 | `a.MANTAS_ACCT_HOLDR_TYPE_CD in (...'CR'...)` | No accounts with holder type 'CR' |
| Product types | ~75 | `t.MANTAS_TRXN_PRDCT_CD in (...'CASH-EQ-*', 'CHECK', ...)` | No transactions matching these product codes |
| Thresholds | ~419-426 | `One_Day_Amt between 2500-2999 OR between 8999-9999` | Transaction amounts don't fall in these ranges |
| Min attempts | ~442 | `Rec_Ct >= 2` | Customer has fewer than 2 records |

---

## Step 4: CTE Parser

### Input
`resolved_function_dataset_*.sql` (28,245 chars)

### What It Does
1. Uses sqlglot to parse `WITH All_Trxn_B AS (...)`
2. Extracts CTE body → saves `001_All_Trxn_B.sql` (30,331 bytes)
3. Extracts final SELECT → saves `final_query.sql` (1,578 bytes)
4. Saves `dependencies.json`

### Output Files
```
parsed_ctes/
├── 001_All_Trxn_B.sql    — CTE body (the big UNION ALL query)
├── final_query.sql       — Final SELECT that uses All_Trxn_B
└── dependencies.json     — {} (no CTE dependencies, only 1 CTE)
```

---

## Step 5: CTE Executer

### What It Does
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

## Step 6: SQL Diagnostics (Generic Engine) — Detailed with Line Numbers

### Entry Point
```python
# orchestrator.py line 871
if state.get("multi_query") and state.get("resolved_function_sql"):
    return _step_generic_sql_diagnostics(state)
```

### 6a: Build SQL Tree
The generic engine receives the **CTE body SQL** (`All_Trxn_B`'s content from `001_All_Trxn_B.sql`):

```
root (kind=ROOT)
  └─ DERIVED (kind=DERIVED_TABLE) — FROM (...) t GROUP BY (lines 12-402)
       └─ BRANCH[1] (kind=UNION_BRANCH) — MI branch (lines 14-273)
       └─ BRANCH[2] (kind=UNION_BRANCH) — CASH branch (lines 275-396)
```

### 6b: Walk the Tree
```
Node                     Row Count  Action
────────────────────────────────────────────────────────────────
root                     0          Has 1 child → recurse into FINAL
  └─ FINAL               0          Has 1 child → recurse into DERIVED
       └─ DERIVED        0          Has 2 children → check both
            ├─ BRANCH[1]  0          LEAF → test WHERE conditions
            └─ BRANCH[2]  0          LEAF → test WHERE conditions
```

### 6c: Condition Elimination on BRANCH[1] (MI — Lines 25-84)

The WHERE clause (lines 52-84) is split into individual AND conditions:

| # | Line | Condition | COUNT without it | Blocker? |
|---|------|-----------|-----------------|----------|
| 1 | 53 | `t.BENEF_ACCT_ID = a.ACCT_INTRL_ID` | 0 | No |
| 2 | 54 | `a.prmry_cust_intrl_id is not null` | 0 | No |
| 3 | 55 | `('Y' = 'Y' or t.SRC_SYS_CD in (...))` | 0 | No (always TRUE) |
| 4 | 56 | `t.INTRL_BENEF_ACCT_FL = 'Y'` | 0 | No |
| 5 | 63 | `c.CUST_INTRL_ID = a.PRMRY_CUST_INTRL_ID` | 0 | No |
| 6 | 64 | `a.MANTAS_ACCT_HOLDR_TYPE_CD in ('CR')` | 0 | No |
| 7 | 65 | `c.CUST_EFCTV_RISK_NB <> -2` | 0 | No |
| 8 | **66** | **`('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))`** | **12,345** | **YES** |
| 9 | 67 | `a.MANTAS_ACCT_BUS_TYPE_CD in ('RBK','RBR')` | 0 | No |
| 10 | 70 | `t.MANTAS_POST_DT in (SELECT ... KDD_CAL ...)` | 0 | No |
| 11 | 71 | `t.DATA_DUMP_DT in (SELECT ... KDD_CAL ...)` | 0 | No |
| 12 | 74 | `t.MANTAS_TRXN_PURP_CD = 'GENERAL'` | 0 | No |
| 13 | 75 | `t.MANTAS_TRXN_PRDCT_CD in (...list...)` | 0 | No |
| 14 | 76 | `('Y' = 'Y' or COALESCE(t.TRSTD_TRXN_FL,'N') = 'N')` | 0 | No |
| 15 | 78 | `('Y' = 'Y' or NOT(...BANK_TO_BANK...))` | 0 | No |
| 16 | 80 | `DECODE(...) IS NOT NULL AND DECODE(...) <> 0` | 0 | No |
| 17 | 81 | `'Y' = 'Y'` | 0 | No (always TRUE) |
| 18 | 84 | `t.CXL_PAIR_TRXN_INTRL_ID IS NULL` | 0 | No |

**Line 66 is the killer:**
```sql
-- Line 66
AND ('N' = 'Y' or c.JRSDCN_CD in (SELECT column_value FROM table (cast (Lst('AIIN') as Lst))))
```

### 6d: Same Pattern Repeats in Other Branches

| Branch | Lines | JRSDCN_CD Filter | Why Same Result |
|--------|-------|------------------|-----------------|
| MI Branch 2 | 114-149 | Line 131: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | Same jurisdiction filter |
| MI Branch 3 | 180-207 | Line 191: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | Same jurisdiction filter |
| MI Branch 4 | 237-272 | Line 256: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | Same jurisdiction filter |
| CASH Branch 1 | 308-337 | Line 322: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | Same jurisdiction filter |
| CASH Branch 2 | 364-395 | Line 380: `('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))` | Same jurisdiction filter |

**All 6 branches have the exact same jurisdiction filter at different line numbers, and ALL are blocked by the same condition.**

### 6e: Result
```json
{
  "status": "LOCALIZED",
  "node": "root > DERIVED > BRANCH[1]",
  "elimination": {
    "clause": "WHERE",
    "baseline": 0,
    "conditions": [
      {
        "condition": "('N' = 'Y' or c.JRSDCN_CD in (SELECT column_value FROM table (cast (Lst('AIIN') as Lst))))",
        "row_count_without_this": 12345,
        "likely_blocker": true
      }
    ]
  }
}
```

### 6f: UI Output
```
================================================================================
  ❌ ALERT NOT GENERATING — ROOT CAUSE FOUND
================================================================================
  CTE          : resolved_function
  Failure type : WHERE
  Condition    : ('N' = 'Y' or c.JRSDCN_CD in (...'AIIN'...))
  Dataset Query Line: 66
  Rows before  : 0
  Rows after   : 12,345
  Likely cause : Data insufficient — no records found for jurisdiction 'AIIN'.
                 The CUST table has no rows with JRSDCN_CD = 'AIIN' for batch date 31/01/2017.
================================================================================
```

---

## Why the Jurisdiction Filter is the Most Likely Blocker

From the log file:
```
Line 309: and ('N'='Y' or b.JRSDCN_CD in ('AIIN'))
Line 343: and ('N'='Y' or c.JRSDCN_CD in ('AIIN'))
```

The parameter `p_All_Jurisdictions_Fl = 'N'` means "don't include all jurisdictions, only include the specified list." The specified list is `Lst('AIIN')`.

The SQL becomes:
```sql
AND ('N' = 'Y' or c.JRSDCN_CD in ('AIIN'))
```

Since `'N' = 'Y'` is **FALSE**, this reduces to:
```sql
AND c.JRSDCN_CD in ('AIIN')
```

If the `CUST` table has **no customers** with `JRSDCN_CD = 'AIIN'` for the batch date `31/01/2017`, the entire CTE returns 0 rows.

This is the **classic OFSAA "no data for jurisdiction" issue** — the scenario is configured for jurisdiction 'AIIN' but there's no customer data with that jurisdiction code.

---

## How to Fix This

1. **Check if jurisdiction 'AIIN' exists in the data:**
   ```sql
   SELECT COUNT(*) FROM fccmatomic.CUST WHERE JRSDCN_CD = 'AIIN';
   ```

2. **If count is 0:** The scenario is configured for the wrong jurisdiction. Change `p_Incl_Jurisdictions_Lst` to a jurisdiction that has data, or set `p_All_Jurisdictions_Fl = 'Y'`.

3. **If count > 0:** The issue is elsewhere — check the date range filter (`KDD_CAL`) or the transaction tables (`MI_TRXN`, `CASH_TRXN`).
