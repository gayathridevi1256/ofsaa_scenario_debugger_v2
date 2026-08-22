# OFSAA Scenario Debugger — v2

## What It Is

An automated diagnostic tool for Oracle Financial Services Analytical Applications (OFSAA) AML scenarios. When a scenario generates **zero alerts** during a batch run, the debugger re-executes the scenario SQL against the target Oracle database, decomposes it layer by layer, and pinpoints the exact root cause — eliminating hours of manual log analysis.

## Business Value

| Before | After |
|--------|-------|
| Analysts spend **~2 hours** manually tracing SQL through OFSAA logs | Root cause identified in **~15 minutes** — **8x faster** |
| Multiple teams (AML Ops, DBA, OFSAA Admin) needed to diagnose | **Single click** — one person gets the full picture |
| "Why are there 0 alerts?" remains unanswered across shifts | Structured diagnosis with **resolved table names, line numbers, and verification** |
| Production issues delay AML monitoring compliance | Faster resolution = **reduced regulatory exposure** |

## v2 Highlights

### 1. Threshold Suggestion

When a **threshold condition** (e.g., `t.Cr_Amt >= 25000`) eliminates all rows, the debugger:

- Identifies the specific column and current threshold value
- Queries the actual data distribution (`MIN`, `MAX`, `AVG`, `PERCENTILE`) from the source table
- Suggests alternative threshold values that would allow alerts to fire
- Compares against threshold ranges from `KDD_TSHLD` metadata

**Output:** Actionable recommendations — e.g., *"Reduce Min_Amt_RR from 25,000 to 5,000 to allow 12,387 rows through"*

---

### 2. Data Error — Step Back Analysis

When a CTE or subquery returns **0 rows due to missing source data**, the debugger goes one step back to answer **why the data wasn't loaded**:

| Check | What It Does |
|-------|-------------|
| **Base table count** | Runs `SELECT COUNT(*)` on the raw source table (bypassing filters) |
| **UNION ALL branch breakdown** | Counts rows per branch — shows which views/tables are empty |
| **Date window validation** | Compares source table date columns against the batch date range |
| **JOIN key availability** | Checks if the JOIN columns have matching keys across both sides |
| **Parent CTE status** | Reports whether an upstream CTE already returned 0 rows (cascading failure) |

**Output:**
```
UNION ALL Branches:
  ✗ Branch 1: 0 rows — FROM Wire_Trxn_Vw
  ✗ Branch 2: 0 rows — FROM Wire_Trxn_Vw
  ✓ Branch 8: 12,450 rows — FROM fccmatomic.CASH_TRXN

Source table 'Wire_Trxn_Vw' has 0 rows for batch date window 2026-06-01 to 2026-06-30
Likely cause: ETL job did not populate Wire_Trxn_Vw for this period
```

This lets the AML Ops team immediately know **what** is empty and **why** — instead of guessing between date mismatch, missing ETL, or configuration error.
