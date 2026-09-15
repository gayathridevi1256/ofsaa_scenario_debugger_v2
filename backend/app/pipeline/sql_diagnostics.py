"""
Granular SQL Diagnostics — Condition-level debugging for OFSAA scenario CTEs.

Two failure categories are diagnosed and surfaced:

  1. DATA UNAVAILABLE  — a JOIN or date filter eliminates all rows.
     Output: which table/view has no data, what date range exists.

  2. THRESHOLDS TOO RESTRICTIVE — a range/equality condition in WHERE
     or HAVING eliminates rows that otherwise exist.
     Output: actual MIN/MAX/P25/P75 of each threshold column, plus
     suggested parameter values pulled from KDD_TSHLD_BINDING.
"""

import os
import re
import math
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# DEPENDENCY CHECK
# ──────────────────────────────────────────────────────────────────────────────

def check_dependency_views_exist(conn, cte_sql: str) -> list[str]:
    """Returns names of tables/views referenced in FROM/JOIN that don't exist."""
    cursor = conn.cursor()
    missing = []
    sql_keywords = {"TABLE", "SELECT", "WHERE", "AND", "OR", "NOT", "IN", "AS", "ON", "IS", "NULL", "TRUE", "FALSE", "EXISTS", "BETWEEN", "LIKE", "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "LST"}
    tokens = cte_sql.upper().split()
    referenced = []
    for i, token in enumerate(tokens):
        if token in ("FROM", "JOIN") and i + 1 < len(tokens):
            name = tokens[i + 1].strip("(),")
            if name and name not in sql_keywords and name.isidentifier():
                referenced.append(name)
    for name in set(referenced):
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM (
                    SELECT view_name AS obj_name FROM user_views
                    UNION ALL
                    SELECT table_name AS obj_name FROM user_tables
                ) WHERE obj_name = :1
            """, [name.upper()])
            if cursor.fetchone()[0] == 0:
                missing.append(name)
        except Exception as e:
            logger.warning("Could not check dependency %s: %s", name, e)
            missing.append(name)
        finally:
            cursor.close()
            cursor = conn.cursor()
    cursor.close()
    return missing


# ──────────────────────────────────────────────────────────────────────────────
# EXECUTE COUNT  (wraps SQL in SELECT COUNT(*) FROM (...) t)
# ──────────────────────────────────────────────────────────────────────────────

def execute_count(conn, sql: str, metadata: dict = None, run_logger=None, step=None, label=None) -> int:
    """Executes SELECT COUNT(*) FROM (sql) t and returns the count."""
    if not sql or not sql.strip():
        return 0
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    if not sql:
        return 0
    if metadata:
        business_date = metadata.get("current_business_date")
        if business_date:
            sql = re.sub(
                r'@(?:current_business_date|batch_date|business_date)',
                f"TO_DATE('{business_date}','YYYY-MM-DD')",
                sql, flags=re.IGNORECASE
            )
    if run_logger and step and label:
        run_logger.log_sql(step, label, sql)
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM (\n{sql}\n) t")
        count = cursor.fetchone()[0]
        if run_logger and label:
            run_logger.log(step, f"EXECUTING SQL: {label} — {count:,} rows")
        return count
    except Exception as e:
        logger.debug("execute_count error: %s", e)
        if run_logger and label:
            run_logger.log(step, f"EXECUTING SQL: {label} — ERROR: {e}")
        return 0
    finally:
        cursor.close()


def execute_full_sql(conn, sql: str, metadata: dict = None, run_logger=None, step=None, label=None) -> int:
    """Executes the full SQL as-is and returns the row count. Used for verification."""
    if not sql or not sql.strip():
        return 0
    if metadata:
        business_date = metadata.get("current_business_date")
        if business_date:
            sql = re.sub(
                r'@(?:current_business_date|batch_date|business_date)',
                f"TO_DATE('{business_date}','YYYY-MM-DD')",
                sql, flags=re.IGNORECASE
            )
    if run_logger and step and label:
        run_logger.log_sql(step, label, sql)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        results = cursor.fetchall()
        count = len(results)
        if run_logger and label:
            status = "ALERTS GENERATED" if count > 0 else "NO ALERTS"
            run_logger.log(step, f"VERIFICATION: {label} — {count:,} rows — {status}")
        return count
    except Exception as e:
        logger.debug("execute_full_sql error: %s", e)
        if run_logger and label:
            run_logger.log(step, f"VERIFICATION: {label} — ERROR: {e}")
        return 0
    finally:
        cursor.close()


def _execute_for_verification(conn, sql: str, metadata: dict, run_logger, label: str) -> tuple:
    """Execute verification SQL and return (count, error_message).
    count = -1 on error, error_message = None on success.
    """
    if not sql or not sql.strip():
        return 0, "Empty SQL"
    if metadata:
        business_date = metadata.get("current_business_date")
        if business_date:
            sql = re.sub(
                r'@(?:current_business_date|batch_date|business_date)',
                f"TO_DATE('{business_date}','YYYY-MM-DD')",
                sql, flags=re.IGNORECASE
            )
    if run_logger:
        run_logger.log_sql(6, label, sql)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        results = cursor.fetchall()
        count = len(results)
        if run_logger:
            status = "ALERTS GENERATED" if count > 0 else "NO ALERTS"
            run_logger.log(6, f"VERIFICATION: {label} — {count:,} rows — {status}")
        return count, None
    except Exception as e:
        logger.debug("verification SQL error: %s", e)
        if run_logger:
            run_logger.log(6, f"VERIFICATION: {label} — ERROR: {e}")
        return -1, str(e)
    finally:
        cursor.close()


def verify_killer_condition(
    conn, killer_condition: str, resolved_sql_file: str, metadata: dict,
    run_logger=None, output_dir: str = ""
) -> dict:
    """
    Replaces the killer condition with '1=1' in the resolved function SQL,
    re-executes, and checks if alerts are generated.
    Using '1=1' instead of commenting out keeps AND/OR connectors valid.
    """
    result = {"verified": False, "rows": 0, "message": ""}

    if not os.path.exists(resolved_sql_file):
        result["message"] = f"Resolved SQL file not found: {resolved_sql_file}"
        return result

    with open(resolved_sql_file, "r", encoding="utf-8") as f:
        full_sql = f.read()

    killer_stripped = killer_condition.strip()
    modified_sql = None

    # Strategy 1: exact match with leading whitespace (multiline)
    pattern = re.compile(r'^(\s*)' + re.escape(killer_stripped), re.MULTILINE)
    match = pattern.search(full_sql)
    if match:
        leading_ws = match.group(1)
        modified_sql = pattern.sub(f"{leading_ws}1=1", full_sql, count=1)

    # Strategy 2: linearized whitespace-insensitive match
    if modified_sql is None:
        flex_pattern = r'\s*'.join(re.escape(p) for p in killer_stripped.split())
        flex_re = re.compile(flex_pattern, re.IGNORECASE | re.DOTALL)
        match = flex_re.search(full_sql)
        if match:
            modified_sql = full_sql[:match.start()] + "1=1" + full_sql[match.end():]

    # Strategy 3: plain replace (single occurrence)
    if modified_sql is None:
        if killer_stripped in full_sql:
            modified_sql = full_sql.replace(killer_stripped, "1=1", 1)

    # Strategy 4: multiline DOTALL exact
    if modified_sql is None:
        lines = killer_stripped.split("\n")
        if len(lines) > 1:
            pattern = re.compile(re.escape(killer_stripped), re.DOTALL)
            if pattern.search(full_sql):
                modified_sql = pattern.sub("1=1", full_sql, count=1)

    # Strategy 5: normalized match — handle "NOT col IS NULL" vs "col IS NOT NULL"
    # and other common semantic equivalences
    if modified_sql is None:
        normalized_killer = re.sub(
            r'\bNOT\s+(\S+)\s+IS\s+NULL\b', r'\1 IS NOT NULL',
            killer_stripped, flags=re.IGNORECASE
        )
        if normalized_killer != killer_stripped:
            # Try the normalized version with linear whitespace matching
            flex_pattern = r'\s*'.join(re.escape(p) for p in normalized_killer.split())
            flex_re = re.compile(flex_pattern, re.IGNORECASE | re.DOTALL)
            match = flex_re.search(full_sql)
            if match:
                modified_sql = full_sql[:match.start()] + "1=1" + full_sql[match.end():]

    if modified_sql is None or modified_sql == full_sql:
        result["message"] = f"Could not find killer condition in resolved SQL: {killer_stripped[:80]}..."
        return result

    if run_logger:
        run_logger.log(6, f"VERIFICATION: Replacing killer condition with 1=1:")
        run_logger.log(6, f"  {killer_stripped[:120]}...  →  1=1")

    count, error = _execute_for_verification(
        conn, modified_sql, metadata, run_logger, "verify_killer_replaced"
    )

    if error:
        result["error"] = error
        result["message"] = f"❌ SQL ERROR during verification: {error}"
        return result

    result["verified"] = count > 0
    result["rows"] = count
    if count > 0:
        result["message"] = f"✅ ALERTS GENERATED — {count:,} rows returned with killer condition replaced by 1=1"
    else:
        result["message"] = f"❌ Still NO ALERTS — 0 rows even with killer condition replaced by 1=1"

    return result


def _replace_cte_where_clause(full_sql: str, cte_name: str) -> tuple:
    """
    Finds the CTE by name in the resolved SQL and replaces its top-level
    WHERE clause with 'WHERE 1=1'.
    Returns (modified_sql, error_message). modified_sql is None on failure.
    """
    # Find the CTE definition: CTE_NAME as (  or  CTE_NAME AS (
    # Allow optional comments between 'as' and '(' (common in OFSAA SQL)
    pattern = re.compile(
        r'\b' + re.escape(cte_name) + r'\s+as\s+(?:--[^\n]*\n\s*)*\(',
        re.IGNORECASE
    )
    match = pattern.search(full_sql)
    if not match:
        return None, f"Could not find CTE '{cte_name}' in resolved SQL"

    open_paren_pos = match.end() - 1  # position of '('

    # Find the matching closing paren
    depth = 0
    close_paren_pos = -1
    for i in range(open_paren_pos, len(full_sql)):
        ch = full_sql[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                close_paren_pos = i
                break
    if close_paren_pos == -1:
        return None, f"Could not find closing paren for CTE '{cte_name}'"

    cte_body = full_sql[open_paren_pos + 1:close_paren_pos]

    # Find the top-level WHERE in the CTE body (depth 0 relative to CTE body)
    where_pos = _find_top_level_clause("WHERE", cte_body)
    if where_pos == -1:
        return None, f"CTE '{cte_name}' has no top-level WHERE clause"

    # Find where the WHERE clause ends:
    # at GROUP BY, HAVING, ORDER BY, WINDOW, QUALIFY, or end of CTE body
    end_candidates = []
    for kw in ["GROUP BY", "HAVING", "ORDER BY", "WINDOW", "QUALIFY"]:
        p = _find_top_level_clause(kw, cte_body)
        if p != -1 and p > where_pos:
            end_candidates.append(p)
    where_end = min(end_candidates) if end_candidates else len(cte_body)

    # Replace the WHERE clause with WHERE 1=1
    new_cte_body = cte_body[:where_pos] + "WHERE 1=1\n" + cte_body[where_end:]

    # Reconstruct the full SQL
    new_sql = full_sql[:open_paren_pos + 1] + new_cte_body + full_sql[close_paren_pos:]
    return new_sql, ""


def _verify_where_combination(
    conn, resolved_sql_file: str, where_conditions: list[str],
    metadata: dict, run_logger=None, cte_name: str = ""
) -> dict:
    """
    Replaces the CTE's entire WHERE clause with 'WHERE 1=1' in the resolved
    function SQL and re-executes to verify alerts would be generated when
    the WHERE combination is removed.
    Uses CTE-name-based WHERE clause replacement to avoid malformed SQL
    (dangling AND/OR, partial multi-line comments) that occurred with the
    previous per-condition comment-out approach.
    """
    total = len(where_conditions)
    result = {"verified": False, "rows": 0, "message": "",
              "conditions_commented": 0, "conditions_total": total}

    if not os.path.exists(resolved_sql_file):
        result["message"] = f"Resolved SQL file not found: {resolved_sql_file}"
        return result

    if not where_conditions:
        result["message"] = "No WHERE conditions provided to verify"
        return result

    if not cte_name:
        result["message"] = "No CTE name provided for WHERE clause replacement"
        return result

    with open(resolved_sql_file, "r", encoding="utf-8") as f:
        full_sql = f.read()

    modified_sql, err = _replace_cte_where_clause(full_sql, cte_name)

    if modified_sql is None:
        result["message"] = f"Could not replace WHERE clause: {err}"
        if run_logger:
            run_logger.log(6, f"VERIFICATION: {err}")
        return result

    result["conditions_commented"] = total  # entire WHERE replaced

    if run_logger:
        run_logger.log(6, f"VERIFICATION: Replaced entire WHERE clause of CTE '{cte_name}' with WHERE 1=1 ({total} conditions removed)")

    count, error = _execute_for_verification(
        conn, modified_sql, metadata, run_logger, "verify_where_combination"
    )

    if error:
        result["error"] = error
        result["message"] = f"❌ SQL ERROR during verification: {error}"
        return result

    result["verified"] = count > 0
    result["rows"] = count
    if count > 0:
        result["message"] = f"✅ ALERTS GENERATED — {count:,} rows returned with WHERE clause replaced by 1=1"
    else:
        result["message"] = f"❌ Still NO ALERTS — 0 rows even with WHERE clause replaced by 1=1"

    return result


# ──────────────────────────────────────────────────────────────────────────────
# SQL STRUCTURE PARSER
# ──────────────────────────────────────────────────────────────────────────────

def _has_top_level_derived_table(sql: str) -> bool:
    """Detect if the outermost FROM clause starts with a subquery: FROM (...)."""
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    from_pos = _find_top_level_clause("FROM", clean)
    if from_pos == -1:
        return False
    after_from = clean[from_pos + 4:].lstrip()
    return after_from.startswith("(")


def _extract_derived_table_inner_sql(sql: str) -> str | None:
    """Extract the inner subquery SQL from SELECT ... FROM (inner_sql) alias WHERE ..."""
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    from_pos = _find_top_level_clause("FROM", clean)
    if from_pos == -1:
        return None
    search_start = from_pos + 4
    i = search_start
    while i < len(clean) and clean[i] in (" ", "\t", "\n", "\r"):
        i += 1
    if i >= len(clean) or clean[i] != "(":
        return None
    depth = 0
    inner_start = i + 1
    while i < len(clean):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
            if depth == 0:
                return clean[inner_start:i].strip()
        i += 1
    return None


def _sample_inner_query_data(conn, inner_sql: str, metadata: dict, run_logger=None, step=6, known_row_count: int = None) -> dict:
    """Sample data from the inner subquery to inspect column values for threshold analysis."""
    result = {"sample_rows": [], "column_stats": {}, "row_count": 0}
    try:
        total_rows = known_row_count
        if total_rows is None:
            count_sql = f"SELECT COUNT(*) FROM (\n{inner_sql}\n) t"
            if run_logger:
                run_logger.log_sql(step, "sample_inner_count", count_sql)
            cursor = conn.cursor()
            cursor.execute(count_sql)
            total_rows = cursor.fetchone()[0]
            cursor.close()
        result["row_count"] = total_rows
        if total_rows == 0:
            return result
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT * FROM (\n{inner_sql}\n) t FETCH FIRST 10 ROWS ONLY")
        except Exception:
            try:
                cursor.execute(f"SELECT * FROM (\n{inner_sql}\n) t ORDER BY 1 FETCH FIRST 10 ROWS ONLY")
            except Exception as e2:
                logger.debug("sample query failed: %s", e2)
                if run_logger:
                    run_logger.log(step, f"  Sample query failed: {e2}")
                cursor.close()
                return result
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        def _json_safe(v):
            if hasattr(v, 'isoformat'):
                return v.isoformat()
            if hasattr(v, 'strftime'):
                return v.strftime('%Y-%m-%d')
            return v
        result["sample_rows"] = [{k: _json_safe(v) for k, v in zip(columns, row)} for row in rows]
        numeric_cols = []
        type_debug = []
        for desc in cursor.description:
            type_code = desc[1]
            type_debug.append(f"{desc[0]}={type_code}")
            if type_code is not None:
                type_name = str(type_code).upper()
                if any(kw in type_name for kw in ("NUMBER", "INT", "FLOAT", "DECIMAL", "NUMERIC", "DOUBLE")):
                    numeric_cols.append(desc[0])
        if run_logger and type_debug:
            run_logger.log(step, f"  Column types: {', '.join(type_debug)}")
        if numeric_cols:
            # Attempt 1: full stats with percentiles (P10/P25/P50/P75/P90) — used
            # by compute_threshold_kill_suggestion() to pick a safe, rounded
            # suggested threshold value instead of the raw min/max. Still a
            # single query over the full population, same as the plain
            # min/max/avg version below — no extra DB round trip. Multiple
            # PERCENTILE_CONT expressions across multiple columns in one flat
            # SELECT (no GROUP BY) is a new shape for this codebase even
            # though the single-column form is proven elsewhere (see
            # _query_column_stats), so this falls back to the plain
            # min/max/avg query on any failure rather than losing stats
            # entirely.
            pct_col_list = ", ".join([
                f"MIN({c}) AS min_{c}, MAX({c}) AS max_{c}, AVG({c}) AS avg_{c}, "
                f"PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY {c}) AS p10_{c}, "
                f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {c}) AS p25_{c}, "
                f"PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {c}) AS p50_{c}, "
                f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {c}) AS p75_{c}, "
                f"PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY {c}) AS p90_{c}"
                for c in numeric_cols
            ])
            stats_sql = f"SELECT {pct_col_list} FROM (\n{inner_sql}\n) t"
            got_percentiles = False
            try:
                if run_logger:
                    run_logger.log_sql(step, "sample_inner_stats", stats_sql)
                cursor.execute(stats_sql)
                stats_row = cursor.fetchone()
                idx = 0
                for c in numeric_cols:
                    result["column_stats"][c] = {
                        "min": stats_row[idx], "max": stats_row[idx + 1], "avg": stats_row[idx + 2],
                        "p10": stats_row[idx + 3], "p25": stats_row[idx + 4], "p50": stats_row[idx + 5],
                        "p75": stats_row[idx + 6], "p90": stats_row[idx + 7],
                    }
                    idx += 8
                got_percentiles = True
            except Exception as e1:
                logger.debug("Percentile stats failed, falling back to min/max/avg: %s", e1)
                if run_logger:
                    run_logger.log(step, f"  Percentile stats failed, falling back to min/max/avg: {e1}")

            if not got_percentiles:
                # Attempt 2: plain min/max/avg (original behavior)
                col_list = ", ".join([
                    f"MIN({c}) AS min_{c}, MAX({c}) AS max_{c}, AVG({c}) AS avg_{c}"
                    for c in numeric_cols
                ])
                stats_sql = f"SELECT {col_list} FROM (\n{inner_sql}\n) t"
                if run_logger:
                    run_logger.log_sql(step, "sample_inner_stats_fallback", stats_sql)
                cursor.execute(stats_sql)
                stats_row = cursor.fetchone()
                idx = 0
                for c in numeric_cols:
                    result["column_stats"][c] = {
                        "min": stats_row[idx], "max": stats_row[idx + 1], "avg": stats_row[idx + 2],
                        "p10": None, "p25": None, "p50": None, "p75": None, "p90": None,
                    }
                    idx += 3
        cursor.close()
        if run_logger:
            run_logger.log(step, f"  Data sample: {total_rows:,} total rows, {len(numeric_cols)} numeric columns analyzed")
    except Exception as e:
        logger.debug("sample_inner_query_data error: %s", e)
        if run_logger:
            run_logger.log(step, f"  Data sample failed: {e}")
    return result


def _check_threshold_kill(sample_data: dict, outer_sql: str, run_logger=None, step=6) -> dict | None:
    """Check if threshold conditions in the outer WHERE clause kill all rows from the inner query."""
    if not sample_data or sample_data.get("row_count", 0) == 0:
        msg = "  Threshold check SKIPPED: no sample data or 0 rows"
        if run_logger:
            run_logger.log(step, msg)
        else:
            print(msg)
        return None
    where_pos = _find_top_level_where(outer_sql)
    if where_pos == -1:
        msg = "  Threshold check SKIPPED: no WHERE clause found"
        if run_logger:
            run_logger.log(step, msg)
        else:
            print(msg)
        return None
    where_block = outer_sql[where_pos + 5:].strip()
    order_pos = _find_top_level_clause("ORDER BY", where_block)
    if order_pos != -1:
        where_block = where_block[:order_pos].strip()
    conditions = _split_and_conditions(where_block)
    msg = f"  Threshold check: {len(conditions)} conditions, column_stats keys: {list(sample_data.get('column_stats', {}).keys())}"
    if run_logger:
        run_logger.log(step, msg)
    else:
        print(msg)
    threshold_patterns = [
        (re.compile(r'(\w+(?:\.\w+)?)\s*>=\s*(\d+)', re.IGNORECASE), ">="),
        (re.compile(r'(\w+(?:\.\w+)?)\s*<=\s*(\d+)', re.IGNORECASE), "<="),
        (re.compile(r'(\w+(?:\.\w+)?)\s*>\s*(\d+)', re.IGNORECASE), ">"),
        (re.compile(r'(\w+(?:\.\w+)?)\s*<\s*(\d+)', re.IGNORECASE), "<"),
    ]
    killers = []
    seen = set()
    stats = sample_data.get("column_stats", {})
    stats_upper = {k.upper(): v for k, v in stats.items()}
    for cond in conditions:
        for pat, op in threshold_patterns:
            for m in pat.finditer(cond):
                col_name = m.group(1).split(".")[-1]
                threshold_val = float(m.group(2))
                col_stats = stats_upper.get(col_name.upper())
                if run_logger:
                    run_logger.log(step, f"  Regex match: {col_name} {op} {threshold_val}, stats_lookup={col_name.upper()}, found={col_stats is not None}")
                else:
                    print(f"  Regex match: {col_name} {op} {threshold_val}, stats_lookup={col_name.upper()}, found={col_stats is not None}")
                if col_stats:
                    max_val = col_stats.get("max")
                    if max_val is not None:
                        killer_key = (col_name.upper(), op, threshold_val)
                        if killer_key in seen:
                            continue
                        seen.add(killer_key)
                        if (op in (">=", ">") and max_val < threshold_val) or \
                           (op in ("<=", "<") and col_stats.get("min", 0) > threshold_val):
                            killers.append({
                                "condition": cond.strip(),
                                "column": col_name,
                                "threshold": threshold_val,
                                "actual_max": max_val,
                                "actual_min": col_stats.get("min"),
                                "operator": op,
                                "p10": col_stats.get("p10"), "p25": col_stats.get("p25"),
                                "p50": col_stats.get("p50"), "p75": col_stats.get("p75"),
                                "p90": col_stats.get("p90"),
                                "population_rows": sample_data.get("row_count", 0),
                            })
                            if run_logger:
                                run_logger.log(step, f"  KILLER FOUND: {col_name} {op} {threshold_val} (actual max={max_val})")
                            else:
                                print(f"  KILLER FOUND: {col_name} {op} {threshold_val} (actual max={max_val})")
    if killers:
        return {
            "failure_type": "THRESHOLD_KILL",
            "killers": killers,
            "sample_rows": sample_data.get("sample_rows", [])[:5],
            "column_stats": sample_data.get("column_stats", {}),
            "total_inner_rows": sample_data.get("row_count", 0),
        }
    msg = f"  No threshold killers found after checking {len(conditions)} conditions"
    if run_logger:
        run_logger.log(step, msg)
    else:
        print(msg)
    return None


# ----------------------------------------------------------------------
# THRESHOLD-KILL SUGGESTION ENGINE
# ----------------------------------------------------------------------
# Pure, DB/LLM-free functions that turn a killer condition's real measured
# data into a concrete suggested new threshold value. Single source of
# truth: used both to populate threshold_suggestions[] here (frontend card,
# no LLM involved) and by ai_recommendation_service.py's
# _build_threshold_hint() (AI recommendation prose) — so the two surfaces
# can never numerically disagree.
#
# Deliberately does NOT suggest the raw actual_max/actual_min: for a >=
# condition, pinning the threshold to the exact observed max would only
# ever match the single row that hit that exact value — not a meaningful
# business cutoff, and brittle the moment that exact record ages out.

_EXTREME_GAP_RATIO = 5.0  # threshold vs. observed data gap ratio beyond which
# this is treated as a data-scale/business question, not a simple value
# tweak. A ~10x gap (the case that prompted this) is unambiguous; 5x is a
# defensible cutoff that catches order-of-magnitude mismatches without
# flagging ordinary 2-3x safety margins, which are common and often
# intentional in business threshold design.


def _nice_floor_positive(x: float) -> float:
    """Largest 'nice' number (1/2/5 x 10^n) that is <= x, for x > 0."""
    if x <= 0:
        return 0.0
    e = math.floor(math.log10(x))
    for m in (10, 5, 2, 1):
        n = m * (10 ** e)
        if n <= x + 1e-9:
            return float(n)
    return float(10 ** e)


def _nice_ceil_positive(x: float) -> float:
    """Smallest 'nice' number (1/2/5 x 10^n) that is >= x, for x > 0."""
    if x <= 0:
        return 0.0
    e = math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        n = m * (10 ** e)
        if n >= x - 1e-9:
            return float(n)
    return float(10 ** (e + 1))


def _nice_floor(x: float | None) -> float | None:
    """Sign-aware: 'round down' means algebraically smaller, so a negative x
    rounds to a MORE negative nice number, not toward zero."""
    if x is None:
        return None
    return _nice_floor_positive(x) if x >= 0 else -_nice_ceil_positive(-x)


def _nice_ceil(x: float | None) -> float | None:
    """Sign-aware counterpart of _nice_floor — see its docstring."""
    if x is None:
        return None
    return _nice_ceil_positive(x) if x >= 0 else -_nice_floor_positive(-x)


def _cfg_float(v) -> float | None:
    """Safe parse of KDD_TSHLD's text-typed MIN_VALUE_TX/MAX_VALUE_TX."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_threshold_kill_suggestion(killer: dict, config_entry: dict | None) -> dict:
    """Given a threshold_killers[] entry and its matched KDD_TSHLD config
    entry (or None), computes a concrete suggested new threshold value.

    Returns a dict with:
      - candidate_value: the safe-direction, rounded suggestion (before
        clamping to the configured range)
      - clamped_value: candidate_value clamped into [cfg_min, cfg_max] when
        that range is known; None if escalate is True
      - clamped: whether clamping actually changed the value
      - escalate: True if no value within the configured legal range would
        admit any of the real observed data — a numeric tweak alone can't
        fix this, it needs the allowed range widened or the environment's
        data reviewed
      - gap_ratio / extreme_gap: how many multiples apart the *currently
        configured* threshold is from the real observed boundary — when
        extreme, the suggestion should be framed as a business question,
        not a confident directive
      - cfg_min / cfg_max / population_rows: passed through for display
    """
    op = killer.get("operator") or ">="
    threshold = killer.get("threshold")
    actual_max = killer.get("actual_max")
    actual_min = killer.get("actual_min")
    cfg_min = _cfg_float(config_entry.get("min")) if config_entry else None
    cfg_max = _cfg_float(config_entry.get("max")) if config_entry else None

    lower_bound = op in (">=", ">")
    boundary = actual_max if lower_bound else actual_min

    # 1. Safe-direction rounding, with an explicit invariant check + exact
    #    boundary fallback if rounding somehow breaks the invariant.
    candidate = _nice_floor(boundary) if lower_bound else _nice_ceil(boundary)
    invariant_ok = (
        candidate is not None and boundary is not None and
        (candidate <= actual_max if lower_bound else candidate >= actual_min)
    )
    if not invariant_ok:
        candidate = boundary

    # 2. Clamp into the KDD_TSHLD-configured legal range, if any.
    clamped = candidate
    escalate = False
    if clamped is not None:
        if lower_bound:
            if cfg_max is not None:
                clamped = min(clamped, cfg_max)
            if cfg_min is not None:
                clamped = max(clamped, cfg_min)
            escalate = actual_max is None or clamped > actual_max
        else:
            if cfg_min is not None:
                clamped = max(clamped, cfg_min)
            if cfg_max is not None:
                clamped = min(clamped, cfg_max)
            escalate = actual_min is None or clamped < actual_min

    # 3. Gap ratio between the CURRENTLY CONFIGURED threshold and the real
    #    observed boundary (not the suggested value) — flags when the
    #    mismatch is large enough to be a data-scale/business question.
    gap_ratio = None
    if lower_bound and threshold is not None:
        gap_ratio = float("inf") if not actual_max else abs(threshold / actual_max)
    elif not lower_bound and threshold not in (None, 0):
        gap_ratio = float("inf") if actual_min is None else abs(actual_min / threshold)
    extreme_gap = gap_ratio is not None and gap_ratio >= _EXTREME_GAP_RATIO

    return {
        "column": killer.get("column"), "operator": op, "current_threshold": threshold,
        "candidate_value": candidate,
        "clamped_value": clamped if not escalate else None,
        "clamped": clamped != candidate,
        "escalate": escalate,
        "gap_ratio": gap_ratio, "extreme_gap": extreme_gap,
        "cfg_min": cfg_min, "cfg_max": cfg_max,
        "population_rows": killer.get("population_rows"),
        "boundary": boundary,
        "reference_percentiles": (
            {"p75": killer.get("p75"), "p90": killer.get("p90")} if lower_bound
            else {"p10": killer.get("p10"), "p25": killer.get("p25")}
        ),
    }


def _match_threshold_config_entry(killer: dict, threshold_config: dict) -> dict | None:
    """Inclusion-based column<->KDD_TSHLD name matching (names rarely match
    the raw SQL column exactly) — mirrors the matching already done inline
    where threshold_config is built (see diagnose_inner_query_sequence),
    but resolved per-killer since threshold_config there is a flat
    {tshld_name: entry} dict, not indexed by column."""
    col_upper = (killer.get("column") or "").upper()
    if not col_upper:
        return None
    for tshld_name, entry in (threshold_config or {}).items():
        name_upper = (tshld_name or "").upper()
        if name_upper and (name_upper in col_upper or col_upper in name_upper):
            return entry
    return None


def _n(v) -> str:
    """Format a number for display: thousands separator, 2 decimals only
    when not a whole number."""
    if v is None:
        return "?"
    if float(v) == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _format_threshold_kill_suggestion_text(killer: dict, calc: dict) -> str:
    """Short, deterministic sentence for the frontend's threshold_suggestions
    card — no LLM involved. Mirrors the three cases _build_threshold_hint()
    renders in ai_recommendation_service.py, just terser."""
    col = killer.get("column") or "?"
    op = calc["operator"]
    lower_bound = op in (">=", ">")
    boundary_label = "max" if lower_bound else "min"
    pop = calc.get("population_rows")
    pop_txt = f" across {pop:,} rows" if pop else ""

    if calc["escalate"]:
        return (
            f"No value within the configured allowed range ({_n(calc['cfg_min'])}–{_n(calc['cfg_max'])}) "
            f"would admit the real observed data (observed {boundary_label}={_n(calc['boundary'])}{pop_txt}) "
            f"— escalate to widen the allowed range or review the environment's data scale."
        )
    if calc["extreme_gap"]:
        ratio = calc["gap_ratio"]
        ratio_txt = f"~{ratio:.1f}x" if ratio not in (None, float("inf")) else "an extreme multiple of"
        return (
            f"Configured threshold ({_n(calc['current_threshold'])}) is {ratio_txt} the observed "
            f"{boundary_label} ({_n(calc['boundary'])}{pop_txt}) — this looks like a data-scale "
            f"mismatch; verify with the business before changing the value."
        )
    direction = "lower" if lower_bound else "raise"
    clamp_note = " (kept within the configured allowed range)" if calc["clamped"] else ""
    return (
        f"Actual {boundary_label} observed: {_n(calc['boundary'])}{pop_txt}. "
        f"Safe suggestion: {direction} threshold to {_n(calc['clamped_value'])}{clamp_note}."
    )


def _find_top_level_where(sql: str) -> int:
    """Find the outermost WHERE clause. For derived-table queries (FROM (...)),
    the outer WHERE is at depth 1, not depth 0."""
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    pattern = re.compile(r'\bWHERE\b', re.IGNORECASE)
    found_depth1 = -1
    for m in pattern.finditer(clean):
        depth = clean[:m.start()].count('(') - clean[:m.start()].count(')')
        if depth == 0:
            return m.start()
        if depth == 1 and found_depth1 == -1:
            found_depth1 = m.start()
    return found_depth1


def _find_top_level_clause(keyword: str, text: str) -> int:
    """Finds a top-level SQL keyword (depth 0), skips occurrences inside subqueries."""
    pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
    for m in pattern.finditer(text):
        depth = text[:m.start()].count('(') - text[:m.start()].count(')')
        if depth == 0:
            return m.start()
    return -1


def parse_sql_structure(sql: str) -> dict:
    """
    Parses a SQL statement into its structural components.
    Returns: base_from, joins, from_with_joins, where_conditions, having_conditions, before_having.
    """
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()

    select_pos = _find_top_level_clause("SELECT",   clean)
    from_pos   = _find_top_level_clause("FROM",     clean)
    where_pos  = _find_top_level_clause("WHERE",    clean)
    group_pos  = _find_top_level_clause("GROUP BY", clean)
    having_pos = _find_top_level_clause("HAVING",   clean)
    order_pos  = _find_top_level_clause("ORDER BY", clean)
    union_pos  = _find_top_level_clause("UNION",    clean)

    empty = {
        "base_from": "", "joins": [], "from_with_joins": "",
        "where_conditions": [], "having_conditions": [], "before_having": clean
    }
    if select_pos == -1 or from_pos == -1:
        return empty

    select_sql = clean[select_pos:from_pos].strip()
    end_of_from = next(
        (p for p in [where_pos, group_pos, having_pos, order_pos] if p != -1),
        len(clean)
    )
    from_block = clean[from_pos:end_of_from].strip()

    join_split_pattern = re.compile(
        r'(?=\b(?:LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|'
        r'INNER\s+JOIN|OUTER\s+JOIN|CROSS\s+JOIN|FULL\s+(?:OUTER\s+)?JOIN|JOIN)\b)',
        re.IGNORECASE
    )
    parts     = join_split_pattern.split(from_block)
    base_from = parts[0].strip()
    joins     = [p.strip() for p in parts[1:] if p.strip()]

    group_block = ""
    if group_pos != -1:
        end_of_group = next(
            (p for p in [having_pos, order_pos] if p != -1),
            len(clean)
        )
        group_block = clean[group_pos:end_of_group].strip()

    from_with_joins = select_sql + "\n" + from_block
    from_with_joins_and_group = select_sql + "\n" + from_block
    if group_block:
        from_with_joins_and_group += "\n" + group_block

    where_conditions = []
    if where_pos != -1:
        end_of_where = min(
            (p for p in [union_pos, group_pos, having_pos, order_pos] if p != -1 and p > where_pos),
            default=len(clean)
        )
        where_block = clean[where_pos:end_of_where].strip()
        where_block = re.sub(r'^\s*WHERE\s+', '', where_block, flags=re.IGNORECASE).strip()
        where_conditions = _split_and_conditions(where_block)

    having_conditions = []
    before_having = clean
    if having_pos != -1:
        end_of_having = order_pos if order_pos != -1 else len(clean)
        having_block = clean[having_pos:end_of_having].strip()
        having_block = re.sub(r'^\s*HAVING\s+', '', having_block, flags=re.IGNORECASE).strip()
        having_conditions = _split_and_conditions(having_block)
        before_having = clean[:having_pos].strip()

    return {
        "base_from":          base_from,
        "joins":              joins,
        "from_with_joins":    from_with_joins,
        "from_with_joins_and_group": from_with_joins_and_group,
        "where_conditions":   where_conditions,
        "having_conditions":  having_conditions,
        "before_having":      before_having,
        "group_by":           group_block,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CONDITION SPLITTERS
# ──────────────────────────────────────────────────────────────────────────────

def _split_conditions_by(text: str, keyword: str) -> list[str]:
    """Generic splitter — splits text by the given keyword at depth 0."""
    if not text:
        return []
    kw = keyword.upper().strip()
    klen = len(kw)
    conditions, current, depth, i = [], "", 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif (
            depth == 0
            and text[i:i + klen].upper() == kw
            and (i == 0 or not text[i - 1].isalnum() and text[i - 1] != '_')
        ):
            if current.strip():
                conditions.append(current.strip())
            current = ""
            i += klen
            continue
        else:
            current += ch
        i += 1
    if current.strip():
        conditions.append(current.strip())
    return conditions


def _split_and_conditions(text: str) -> list[str]:
    return _split_conditions_by(text, " AND ")


def _split_or_conditions(text: str) -> list[str]:
    """Splits a condition block by top-level OR, respecting parentheses."""
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1]
        # Verify the outer parens wrap the whole expression
        depth = 0
        for ch in inner:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            if depth < 0:
                break
        else:
            stripped = inner.strip()
    return _split_conditions_by(stripped, " OR ")


def _unwrap_or_split_and(condition: str) -> list[str]:
    """
    If a condition is a parenthesised AND block, strips parens and splits by AND.
    Returns the original list if it can't be split further.
    """
    stripped = condition.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1].strip()
        parts = _split_and_conditions(inner)
        if len(parts) > 1:
            return parts
    return [condition]


# ──────────────────────────────────────────────────────────────────────────────
# LIKELY CAUSE
# ──────────────────────────────────────────────────────────────────────────────

def _likely_cause(condition: str) -> str:
    c = condition.upper()

    # ── Jurisdiction / flag guard: ('N' = 'Y' OR col.JRSDCN_CD IN ('CODE')) ──
    jrsdcn_m = re.search(r"JRSDCN_CD\s+IN\s*\(\s*'([^']+)'", condition, re.IGNORECASE)
    if jrsdcn_m:
        code = jrsdcn_m.group(1)
        return (
            f"Data insufficient — no records found for jurisdiction '{code}'. "
            f"The account table has no rows with JRSDCN_CD = '{code}' for this batch date."
        )

    # ── Always-false guard with inner filter: ('N' = 'Y' OR <real_filter>) ──
    guard_m = re.match(r"^\(\s*'N'\s*=\s*'Y'\s+OR\s+(.+)\)\s*$", condition.strip(), re.IGNORECASE)
    if guard_m:
        inner = guard_m.group(1).strip()
        ji = re.search(r"JRSDCN_CD\s+IN\s*\(\s*'([^']+)'", inner, re.IGNORECASE)
        if ji:
            code = ji.group(1)
            return (
                f"Data insufficient — no records found for jurisdiction '{code}'. "
                f"The account table has no rows with JRSDCN_CD = '{code}' for this batch date."
            )
        cd_m = re.search(r"(\w+_CD)\s+IN\s*\(([^)]+)\)", inner, re.IGNORECASE)
        if cd_m:
            col, vals = cd_m.group(1), cd_m.group(2)
            return f"Data insufficient — no records match {col} IN ({vals})."
        return f"Guard condition is active; '{inner}' returns 0 rows — data insufficient for this filter."

    if "SELECT" in c:
        if any(k in c for k in ["MIN_DT", "MAX_DT", "DATE", "DT"]):
            return "Date filter too restrictive — no rows fall in the date range returned by the subquery"
        return "Subquery returns no rows or NULL — condition filters everything out"

    # Threshold checks BEFORE IN checks — thresholds are more actionable
    if any(op in condition for op in [">=", "<=", ">", "<"]):
        if any(d in c for d in ["_DT", "DATE", "TRUNC", "SYSDATE"]):
            return "Date comparison too restrictive — data does not fall in the expected range"
        return "Thresholds are set too high — range condition eliminates all rows"

    if " IN " in c and "(" in c:
        return "IN list has no matching values in the data"
    if "=" in c and "NULL" not in c:
        return "Equality filter has no matching values in the data"
    if "IS NULL" in c or "IS NOT NULL" in c:
        return "NULL check filtering out all rows — unexpected NULLs or non-NULLs in data"
    if "COALESCE" in c or "NVL" in c:
        return "COALESCE/NVL condition filtering out all rows — check default value logic"
    if "LIKE" in c:
        return "LIKE pattern has no matching values in the data"
    return "Condition is too restrictive — no rows satisfy this filter"


def _explain_where_equality(conn, condition: str, base_from: str, metadata: dict = None) -> str | None:
    """
    For a WHERE equality that looks like a cross-table join
    (e.g. t.BENEF_ACCT_ID = a.ACCT_INTRL_ID), runs a targeted
    data-availability check and returns a descriptive sentence,
    or None if we can't determine anything useful.
    """
    eq_match = re.search(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', condition)
    if not eq_match:
        return None

    left_alias  = eq_match.group(1)
    left_col    = eq_match.group(2)
    right_alias = eq_match.group(3)
    right_col   = eq_match.group(4)

    alias_map = _extract_alias_map(base_from)
    left_table  = alias_map.get(left_alias.lower(), left_alias)
    right_table = alias_map.get(right_alias.lower(), right_alias)

    try:
        # Count distinct keys on each side
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(DISTINCT {left_alias}.{left_col}) FROM {left_table} {left_alias}")
        left_keys = cursor.fetchone()[0]
        cursor.close()

        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(DISTINCT {right_alias}.{right_col}) FROM {right_table} {right_alias}")
        right_keys = cursor.fetchone()[0]
        cursor.close()

        # Count matching pairs with a simple inner join
        cursor = conn.cursor()
        match_sql = f"""
            SELECT COUNT(*)
            FROM {left_table} {left_alias}
            INNER JOIN {right_table} {right_alias}
                ON {left_alias}.{left_col} = {right_alias}.{right_col}
        """
        cursor.execute(match_sql)
        matches = cursor.fetchone()[0]
        cursor.close()

        if matches == 0:
            # Determine which side is "empty" relative to the other
            left_short  = left_table.split('.')[-1].upper()
            right_short = right_table.split('.')[-1].upper()
            if left_keys == 0 and right_keys == 0:
                return f"JOIN {condition} produces 0 rows — both {left_short} and {right_short} are empty."
            if left_keys == 0:
                return f"JOIN {condition} produces 0 rows — {left_short} has no values for {left_col}."
            if right_keys == 0:
                return f"JOIN {condition} produces 0 rows — {right_short} has no values for {right_col}."
            return (
                f"JOIN {condition} produces 0 rows — "
                f"{left_short} has {left_keys:,} distinct {left_col} values but none match "
                f"{right_short}'s {right_keys:,} distinct {right_col} values."
            )

        # Matches > 0 but the full query still returned 0 — other filters are killing it
        return (
            f"Equality filter {condition} has {matches:,} matching rows in isolation, "
            f"but other WHERE / JOIN conditions eliminate all rows."
        )
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# THRESHOLD PARAMETER LOOKUP
# ──────────────────────────────────────────────────────────────────────────────

def _get_threshold_bindings(conn, tshld_set_id) -> dict:
    """
    Fetches {BINDING_NM: BINDING_VALUE_TX} from KDD_TSHLD_BINDING.
    Returns empty dict on any error.
    """
    if not tshld_set_id:
        return {}
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT BINDING_NM, BINDING_VALUE_TX
            FROM   fccmatomic.KDD_TSHLD_BINDING
            WHERE  TSHLD_SET_ID = :1
        """, [str(tshld_set_id)])
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception as e:
        logger.debug("Could not fetch threshold bindings: %s", e)
        return {}
    finally:
        cursor.close()


def _get_threshold_config(conn, tshld_set_id) -> dict:
    """
    Fetches {TSHLD_NM: {curr, min, max, desc, display_name, unit}} from
    KDD_TSHLD for the given threshold set. Used to show configured value
    range alongside the current threshold setting, and to give the AI
    recommendation a plain-English description of what the threshold means.
    """
    if not tshld_set_id:
        return {}
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT TSHLD_NM, CURR_VALUE_TX, MIN_VALUE_TX, MAX_VALUE_TX,
                   DESC_TX, DPLY_NM, UNIT_TX
            FROM   fccmatomic.KDD_TSHLD
            WHERE  TSHLD_SET_ID = :1
        """, [str(tshld_set_id)])
        result = {}
        for row in cursor.fetchall():
            name, curr, mn, mx, desc, display_name, unit = row
            result[name] = {
                "curr": curr, "min": mn, "max": mx,
                "desc": desc, "display_name": display_name, "unit": unit,
            }
        return result
    except Exception as e:
        logger.debug("Could not fetch threshold config: %s", e)
        return {}
    finally:
        cursor.close()


def get_threshold_config(conn, tshld_set_id) -> dict:
    """Public entry point for _get_threshold_config — used by the standalone
    Threshold Tuning feature (backend/app/services/threshold_tuning_service.py)
    to show a scenario's currently configured threshold values independent of
    running a full diagnostic job."""
    return _get_threshold_config(conn, tshld_set_id)


def _extract_main_sql_param_mapping(output_dir: str) -> dict:
    """
    Reads the main SQL from output_dir/extracted_queries/*_main_query_*.sql and
    extracts {col_name_lower: {op: param_name}} from patterns like 'col OP @param'.
    Used to resolve KDD_TSHLD param names for literal-value conditions in the
    dataset SQL (where @params have been substituted with current values).
    """
    extracted_dir = os.path.join(output_dir, "extracted_queries")
    if not os.path.isdir(extracted_dir):
        return {}
    main_files = [
        os.path.join(extracted_dir, f)
        for f in os.listdir(extracted_dir)
        if "main_query" in f.lower() and f.lower().endswith(".sql")
    ]
    if not main_files:
        return {}
    main_sql_path = max(main_files, key=os.path.getmtime)
    try:
        content = open(main_sql_path, encoding="utf-8", errors="replace").read()
    except Exception as e:
        logger.warning("Could not read main SQL for param mapping: %s", e)
        return {}

    # Match: optional_alias.col_name OP @param_name
    pattern = re.compile(r'\b(?:\w+\.)?(\w+)\s*(>=|<=|>|<|=)\s*@(\w+)', re.IGNORECASE)
    mapping: dict = {}   # {col_lower: {op: param_name}}
    for m in pattern.finditer(content):
        col   = m.group(1)
        op    = m.group(2)
        param = m.group(3)
        col_l = col.lower()
        if col_l not in mapping:
            mapping[col_l] = {}
        if op not in mapping[col_l]:           # first occurrence wins (HR before MR/RR)
            mapping[col_l][op] = param
    return mapping


# ──────────────────────────────────────────────────────────────────────────────
# PROACTIVE THRESHOLD TUNING — used by the standalone Threshold Tuning page
# (threshold_tuning_service.py) to recommend a value for EVERY configured
# threshold, not just ones currently causing a diagnosed failure. Reuses
# compute_threshold_kill_suggestion (the same deterministic engine, same
# never-suggest-the-raw-boundary reasoning) once a real column is identified.
# ──────────────────────────────────────────────────────────────────────────────

_PARAM_COND_RE = re.compile(r'\b(?:(\w+)\.)?(\w+)\s*(>=|<=|>|<|=)\s*@(\w+)', re.IGNORECASE)
_FROM_JOIN_RE = re.compile(r'\b(?:FROM|JOIN)\s+([\w.]+)\s+(\w+)\b', re.IGNORECASE)
_SQL_KEYWORDS = {"ON", "WHERE", "AND", "OR", "GROUP", "ORDER", "HAVING", "UNION", "SELECT", "AS"}


_CASE_END_TOKEN_RE = re.compile(r'\bCASE\b|\bEND\b', re.IGNORECASE)


def _extract_case_expression_ending_at(main_sql: str, end_pos: int) -> str | None:
    """Given the position right after a CASE...END expression's closing
    'END' keyword at `end_pos`, scans backward tracking nested CASE/END
    pairs to find the matching opening 'CASE', returning the full
    'CASE...END' text. Real OFSAA scenario SQL commonly compares a
    threshold against a computed ratio this way (e.g. 'CASE WHEN
    Tot_Trans_Amt > 0 THEN Tot_Small_Trans_Amt*100/Tot_Trans_Amt ELSE 0
    END >= @Threshold') rather than a bare column — the naive 'word right
    before the operator' extraction sees only the literal word 'end' in
    that case, useless as a column name. Returns None if no balanced CASE
    is found before end_pos."""
    tokens = list(_CASE_END_TOKEN_RE.finditer(main_sql, 0, end_pos))
    if not tokens or tokens[-1].end() != end_pos:
        return None
    depth = 1
    for tok in reversed(tokens[:-1]):
        if tok.group(0).upper() == 'END':
            depth += 1
        else:  # CASE
            depth -= 1
            if depth == 0:
                return main_sql[tok.start():end_pos]
    return None


def _strip_single_alias_prefix(expr: str) -> str:
    """If `expr` consistently qualifies its column references with exactly
    one table alias (e.g. every column in a CASE expression is written
    'g.Tot_Trans_Amt'), strips that alias prefix throughout. A reconstructed
    standalone sampling query exposes those same columns unqualified — its
    own wrapper alias is never the original scenario SQL's alias — so a
    copied expression needs this to resolve at all."""
    aliases = {m.group(1) for m in re.finditer(r'\b([A-Za-z_]\w*)\.\w', expr)}
    if len(aliases) == 1:
        alias = aliases.pop()
        return re.sub(rf'\b{re.escape(alias)}\.', '', expr)
    return expr


def extract_param_column_bindings(main_sql: str) -> dict:
    """Like _extract_main_sql_param_mapping, but also captures the table
    alias (that function discards it) — needed to resolve a threshold's
    param name to a real physical table, not just a bare column name.
    Returns {param_name: [(alias_or_None, column, operator), ...]}. When the
    comparison is against a 'CASE...END' computed expression rather than a
    bare column, `column` is the full expression text (alias prefixes
    stripped) and `alias` is None — see _extract_case_expression_ending_at."""
    bindings: dict = {}
    for m in _PARAM_COND_RE.finditer(main_sql or ""):
        alias, col, op, param = m.group(1), m.group(2), m.group(3), m.group(4)
        if col.upper() == 'END':
            case_expr = _extract_case_expression_ending_at(main_sql, m.end(2))
            if case_expr:
                alias = None
                col = _strip_single_alias_prefix(case_expr)
        bindings.setdefault(param, []).append((alias, col, op))
    return bindings


def _mask_comment_parens(text: str) -> str:
    """Returns `text` with any '(' or ')' inside a '--' line comment
    replaced by a space — every other character, including newlines, is
    preserved exactly, so character offsets computed against the result
    stay valid for slicing the original `text`. Used to keep paren-depth
    scanning (finding a FROM (...) subquery's true closing paren) from
    being thrown off by parenthetical remarks in SQL comments."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] == '-' and i + 1 < n and text[i + 1] == '-':
            end = text.find('\n', i)
            if end == -1:
                end = n
            for k in range(i, end):
                if out[k] in '()':
                    out[k] = ' '
            i = end
        else:
            i += 1
    return ''.join(out)


def _extract_with_prefix(self_contained_text: str) -> str | None:
    """Given text starting with 'WITH cte1 AS (...), cte2 AS (...), ...
    <outer query>', returns just the CTE-definitions portion — from 'WITH'
    up to (not including) the outer query's own top-level SELECT — so it
    can be spliced onto a different, unrelated tail query to make that
    query independently executable. Finds the boundary by paren-depth
    tracking (comment-masked): each CTE's own SELECT is nested inside its
    '(...)', so the first SELECT keyword seen back at depth 0 is the outer
    query's, marking where the WITH clause ends. Returns None if no WITH
    clause is found (caller should fall back rather than guess)."""
    masked = _mask_comment_parens(self_contained_text)
    with_m = re.search(r'\bWITH\b', masked, re.IGNORECASE)
    if not with_m:
        return None

    # Depth just BEFORE each position (so a SELECT sitting outside every
    # CTE's own parens reads back as depth 0, while one nested inside a
    # CTE's body reads back as depth >= 1).
    depth = 0
    depth_before = [0] * (len(masked) + 1)
    for i, ch in enumerate(masked):
        depth_before[i] = depth
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1

    for m in re.finditer(r'\bSELECT\b', masked[with_m.end():], re.IGNORECASE):
        pos = with_m.end() + m.start()
        if depth_before[pos] == 0:
            return self_contained_text[with_m.start():pos]
    return None


def extract_alias_source_map(sql_text: str) -> dict:
    """Returns {alias_lower: source} where source is either a bare table
    name (str, from a plain 'FROM table alias' / 'JOIN table alias') or a
    dict {"subquery": sql_text} for a derived table ('FROM (<subquery>)
    alias', paren-depth-aware since the subquery body can contain nested
    parens). Both shapes are needed: OFSAA scenario SQL commonly compares
    thresholds against an AGGREGATED column computed in a derived subquery
    (e.g. SUM(...) AS Tot_Trxn_Am_Cdt), not a raw physical-table column —
    confirmed live: every numeric threshold in a real scenario bound to a
    derived-table alias, none to a bare table. Best-effort, not a full
    parser (a crude keyword guard filters out matches where 'alias' is
    actually the next clause keyword).

    A derived table nested inside a larger derived table that opens with a
    WITH clause (defining CTEs the inner one depends on — extremely common
    in OFSAA scenario SQL, e.g. a per-customer aggregation subquery sitting
    inside a WITH-scoped block that defines clndr_vw/Wire_Trxn_Vw/etc.) is
    NOT independently executable: isolating it strips away the CTE
    definitions its FROM clause references, which fails with ORA-00942 (or,
    confirmed live, sometimes the less obvious ORA-00911) once wrapped
    standalone. So every non-self-contained alias here is remapped to the
    smallest self-contained (WITH-opening) ancestor subquery that textually
    contains it — sampling that instead, by the same bare column name,
    which is what actually succeeded in live testing."""
    mapping: dict = {}

    for m in _FROM_JOIN_RE.finditer(sql_text or ""):
        table, alias = m.group(1), m.group(2)
        if alias.upper() not in _SQL_KEYWORDS:
            mapping[alias.lower()] = table

    # Paren-depth scanning below must not be corrupted by parens inside
    # '--' comments (commented-out dead code, bug-ticket notes like
    # "-- Bug#12345 (see JIRA-6789)") — confirmed live: three lines of dead
    # code left as "-- count(distinct(case when..." each contributed two
    # unmatched '(' that silently broke the closing-paren search for an
    # entire outer subquery, so its alias never resolved to anything at
    # all. Scan a comment-masked copy (parens inside comments blanked out,
    # every other character including newlines preserved 1:1) so character
    # offsets stay valid for slicing the real text below.
    masked_sql = _mask_comment_parens(sql_text or "")

    for m in re.finditer(r'\bFROM\s*\(', sql_text or "", re.IGNORECASE):
        open_pos = m.end() - 1
        depth = 0
        close_pos = -1
        for i in range(open_pos, len(masked_sql)):
            if masked_sql[i] == '(':
                depth += 1
            elif masked_sql[i] == ')':
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
        if close_pos == -1:
            continue
        alias_m = re.match(r'\s*(\w+)', sql_text[close_pos + 1:close_pos + 40])
        if alias_m and alias_m.group(1).upper() not in _SQL_KEYWORDS:
            mapping[alias_m.group(1).lower()] = {"subquery": sql_text[open_pos + 1:close_pos]}

    def _is_self_contained(subquery: str) -> bool:
        for line in subquery.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('--'):
                continue
            return stripped[:5].upper() == 'WITH ' or stripped.upper() == 'WITH'
        return False

    subqueries = {a: v["subquery"] for a, v in mapping.items() if isinstance(v, dict)}
    for alias, sq in subqueries.items():
        if _is_self_contained(sq):
            continue
        best_alias, best_len = None, None
        for other_alias, other_sq in subqueries.items():
            if other_alias == alias or not _is_self_contained(other_sq):
                continue
            if sq in other_sq and (best_len is None or len(other_sq) < best_len):
                best_alias, best_len = other_alias, len(other_sq)
        if best_alias:
            # Splice this alias's own body onto the ancestor's WITH-clause
            # CTE definitions, rather than sampling the ancestor wholesale.
            # The ancestor is the OUTER query — it commonly has the
            # scenario's own threshold-elimination WHERE clause applied on
            # top of this alias's aggregation (this alias sits inside a
            # "select ... from (<this alias>) alias where <thresholds>"
            # shape), so sampling it wholesale silently samples the
            # POST-filter population. That's fine by coincidence when
            # something still passes the filter, but confirmed live: for a
            # scenario currently at 0 alerts, the ancestor is empty by
            # definition (it *is* the filtered-to-nothing result), so
            # every sample came back "no rows" — useless for exactly the
            # case (loosening a dead scenario) that most needs it. Reusing
            # just the CTE definitions keeps this alias independently
            # executable while sampling its own true output, not
            # whatever's left after the very filter being tuned.
            with_prefix = _extract_with_prefix(subqueries[best_alias])
            if with_prefix:
                mapping[alias] = {"subquery": f"{with_prefix}\nSELECT * FROM (\n{sq}\n) g_src"}
            else:
                mapping[alias] = mapping[best_alias]

    return mapping


def sample_column_stats(conn, source, column: str) -> dict | None:
    """Real MIN/MAX/percentiles for `column` — the true population, not
    filtered by any of the scenario's other conditions. `source` is either
    a bare table name (str) or a dict {"subquery": sql_text} from
    extract_alias_source_map, in which case the subquery is executed and
    stats are taken over its result (mirrors _sample_inner_query_data's
    approach for a specific failing CTE — here applied proactively to
    whatever derived table the threshold's column actually lives in).
    (Different from _sample_inner_query_data itself: there is no single
    'inner query' to scope to here, since this runs for every threshold,
    not just a specific failing CTE.) Returns None if the query fails (e.g.
    wrong table/column guess from the alias-resolution heuristic) rather
    than raising — callers should treat that threshold as unrecommendable,
    not fail the whole batch."""
    # Alias must start with a letter, not "_" — confirmed live this is the
    # actual cause of an ORA-00911 that looked (misleadingly) like it came
    # from comments/WITH-clauses/PERCENTILE_CONT: identical query content
    # succeeded when the wrapping alias was "x" and failed when it was
    # "_src", with nothing else different.
    from_clause = f"(\n{source['subquery']}\n) x_src" if isinstance(source, dict) else source
    src_label = "<subquery>" if isinstance(source, dict) else source
    cursor = conn.cursor()
    try:
        # Attempt 1: full stats with percentiles. Tries first, falls back
        # to plain min/max/count on any failure (mirrors
        # _query_column_stats's Attempt-1/Attempt-2 pattern) - confirmed
        # live that PERCENTILE_CONT can fail with ORA-00911 against a
        # deeply-nested multi-CTE/UNION-ALL derived table even when a plain
        # MIN/MAX/COUNT over the exact same FROM clause succeeds; the
        # fallback keeps a real recommendation available (with reduced
        # percentile context) rather than losing it outright.
        try:
            cursor.execute(f"""
                SELECT COUNT(*), MIN({column}), MAX({column}),
                       PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY {column}),
                       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {column}),
                       PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {column}),
                       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {column}),
                       PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY {column})
                FROM {from_clause}
                WHERE {column} IS NOT NULL
            """)
            row = cursor.fetchone()
            if row and row[0]:
                total, mn, mx, p10, p25, p50, p75, p90 = row
                return {
                    "population_rows": int(total), "actual_min": mn, "actual_max": mx,
                    "p10": p10, "p25": p25, "p50": p50, "p75": p75, "p90": p90,
                }
        except Exception as e1:
            logger.debug("sample_column_stats percentile query failed for %s.%s, falling back: %s", src_label, column, e1)

        # Attempt 2: plain min/max/count (no percentiles)
        cursor.execute(f"""
            SELECT COUNT(*), MIN({column}), MAX({column})
            FROM {from_clause}
            WHERE {column} IS NOT NULL
        """)
        row = cursor.fetchone()
        if not row or not row[0]:
            return None
        total, mn, mx = row
        return {
            "population_rows": int(total), "actual_min": mn, "actual_max": mx,
            "p10": None, "p25": None, "p50": None, "p75": None, "p90": None,
        }
    except Exception as e2:
        logger.debug("sample_column_stats failed for %s.%s: %s", src_label, column, e2)
        return None
    finally:
        cursor.close()


def _match_param_name(value, threshold_bindings: dict) -> str | None:
    """Returns the KDD_TSHLD_BINDING parameter name whose value matches `value`."""
    if not threshold_bindings:
        return None
    try:
        val_str = str(int(float(value))) if float(value) == int(float(value)) else str(value)
    except Exception:
        val_str = str(value)
    for name, bound_val in threshold_bindings.items():
        if str(bound_val).strip() == val_str:
            return name
    return None


# ──────────────────────────────────────────────────────────────────────────────
# SQL PREPARATION & INJECTION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _find_top_level_keyword(sql: str, *keywords) -> dict:
    """
    Scans sql for the first occurrence of each keyword at paren-depth 0.
    Returns {keyword: char_position, ...} for any that were found.
    """
    upper = sql.upper()
    depth = 0
    positions = {}
    i = 0
    while i < len(upper):
        c = upper[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0:
            for kw in keywords:
                if upper[i:i + len(kw)] == kw:
                    if kw not in positions:
                        positions[kw] = i
                    break
        i += 1
    return positions


def _inject_where_condition(sql: str, condition: str) -> str:
    """
    Adds `condition` to the top-level WHERE clause of sql.
    If no WHERE exists, inserts one before GROUP BY / HAVING / ORDER BY.
    """
    kw_pos = _find_top_level_keyword(sql, "WHERE", "GROUP BY", "HAVING", "ORDER BY")
    if "WHERE" in kw_pos:
        pos = kw_pos["WHERE"] + len("WHERE")
        return sql[:pos] + f" ({condition}) AND " + sql[pos:]
    else:
        insert_at = min(
            (kw_pos[k] for k in ("GROUP BY", "HAVING", "ORDER BY") if k in kw_pos),
            default=len(sql)
        )
        return sql[:insert_at].rstrip() + f" WHERE {condition} " + sql[insert_at:]

def _prepare_sql(source_sql: str, metadata: dict, threshold_bindings: dict = None) -> str:
    """Replace @vars and strip comments so a SQL snippet can be safely executed."""
    safe = source_sql
    if metadata:
        bd = metadata.get("current_business_date")
        date_replacement = f"TO_DATE('{bd}','YYYY-MM-DD')" if bd else "SYSDATE"

        def _replace_var(m):
            name = m.group(1)
            if any(d in name.upper() for d in _DATE_PARAM_WORDS):
                return date_replacement
            if threshold_bindings and name in threshold_bindings:
                try:
                    v = float(threshold_bindings[name])
                    return str(int(v)) if v == int(v) else str(v)
                except (ValueError, TypeError):
                    pass
            return date_replacement

        safe = re.sub(r'@([\w_]+)', _replace_var, safe)
    return re.sub(r'/\*.*?\*/', '', safe, flags=re.DOTALL).strip()


# ──────────────────────────────────────────────────────────────────────────────
# THRESHOLD SIMULATION
# ──────────────────────────────────────────────────────────────────────────────

def _simulate_threshold(
    conn, col_name: str, op: str, current_val: float,
    preceding_sql: str, metadata: dict, threshold_bindings: dict = None,
    full_cte_sql: str = None, kdd_candidate: float = None
) -> dict | None:
    """
    Tries adjusted threshold values to find one that produces rows.

    Primary strategy: substitute the exact threshold value in the full CTE SQL
    (e.g. change  Tot_Small_Trans_Amt >= 5000  to  Tot_Small_Trans_Amt >= 2500)
    then count rows in the modified query.  This works for any column regardless
    of whether it appears in the SELECT list.

    Fallback strategy: inject/wrap using preceding_sql (legacy approach).

    kdd_candidate: a KDD_TSHLD-sourced value to try first (Approach 1 from design).
    """
    if current_val is None:
        return None

    cv = float(current_val)
    cv_str = str(int(cv)) if cv == int(cv) else str(cv)

    safe_full = _prepare_sql(full_cte_sql, metadata, threshold_bindings) if full_cte_sql else None
    safe_prec = _prepare_sql(preceding_sql, metadata, threshold_bindings) if preceding_sql else None

    def _count_by_substitution(val: float) -> int | None:
        """Replace col op current_val → col op val inside the full CTE SQL."""
        if not safe_full:
            logger.warning("SIM [%s %s]: safe_full is None — full_cte_sql not passed", col_name, op)
            return None
        v_str = str(int(val)) if val == int(val) else str(round(val, 4))
        # Group 1 captures optional alias prefix (e.g. "c.") so it is preserved
        pattern = re.compile(
            rf'\b((?:\w+\.)?){re.escape(col_name)}\s*{re.escape(op)}\s*{re.escape(cv_str)}(?!\d)',
            re.IGNORECASE
        )
        modified = pattern.sub(rf'\g<1>{col_name} {op} {v_str}', safe_full)
        if modified == safe_full:
            logger.warning("SIM [%s %s %s]: pattern not found in SQL — cv_str=%r snippet=%r",
                           col_name, op, v_str, cv_str, safe_full[:200])
            return None  # pattern not found — can't substitute
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM ({modified}) _sim_cnt")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.warning("SIM substitution Oracle error (%s %s %s): %s", col_name, op, v_str, e)
            return None
        finally:
            cursor.close()

    def _count_by_injection(val: float) -> int | None:
        """Inject condition into preceding_sql or use outer-subquery filter."""
        if not safe_prec:
            return None
        v_str = str(int(val)) if val == int(val) else str(round(val, 4))
        condition_str = f"{col_name} {op} {v_str}"
        for sql_attempt in [
            f"SELECT COUNT(*) FROM ({_inject_where_condition(safe_prec, condition_str)}) _cnt",
            f"SELECT COUNT(*) FROM ({safe_prec}) _sim WHERE {condition_str}",
        ]:
            cursor = conn.cursor()
            try:
                cursor.execute(sql_attempt)
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            except Exception as e:
                logger.warning("SIM injection Oracle error (%s %s %s): %s", col_name, op, v_str, e)
            finally:
                cursor.close()
        return None

    def _count(val: float) -> int | None:
        c = _count_by_substitution(val)
        if c is None:
            c = _count_by_injection(val)
        return c

    # Build candidate list — KDD_TSHLD value tried first (Approach 1)
    if op in (">=", ">"):
        kdd_vals = [kdd_candidate] if (kdd_candidate is not None and kdd_candidate < cv) else []
        if cv == int(cv) and cv <= 20:
            auto_cands = list(range(int(cv) - 1, -1, -1))
        else:
            auto_cands = [cv * f for f in [0.75, 0.5, 0.25, 0.1, 0.0]]
    elif op in ("<=", "<"):
        kdd_vals = [kdd_candidate] if (kdd_candidate is not None and kdd_candidate > cv) else []
        auto_cands = [cv * f for f in [2, 5, 10, 50, 100]]
    else:
        return None

    kdd_set = set(kdd_vals)
    candidates = kdd_vals + [v for v in auto_cands if v not in kdd_set]

    if not candidates:
        return None  # no meaningful values to try (e.g. current val is 0)

    attempts = []
    found = None
    for val in candidates[:8]:
        count = _count(val)
        if count is None:
            continue
        attempts.append({"val": val, "count": count})
        if count > 0 and found is None:
            found = {"val": val, "count": count}
            break

    if not attempts:
        logger.warning("All simulation strategies failed for %s %s %s", col_name, op, cv_str)

    return {"found": found, "attempts": attempts}


# ──────────────────────────────────────────────────────────────────────────────
# DATA DISTRIBUTION QUERY
# ──────────────────────────────────────────────────────────────────────────────

def _query_column_stats(
    conn, col_name: str, source_sql: str, metadata: dict,
    threshold_bindings: dict = None
) -> dict | None:
    """
    Queries MIN, MAX, COUNT and percentiles for `col_name` from `source_sql`.
    Returns None on error or if the source returns 0 rows.
    """
    safe_sql = _prepare_sql(source_sql, metadata, threshold_bindings)

    cursor = conn.cursor()
    try:
        # Attempt 1: full stats with percentiles
        try:
            cursor.execute(f"""
                SELECT COUNT(*),
                       MIN({col_name}),
                       MAX({col_name}),
                       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col_name}),
                       PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {col_name}),
                       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col_name})
                FROM ({safe_sql}) _stats_src
                WHERE {col_name} IS NOT NULL
            """)
            row = cursor.fetchone()
            if row and row[0]:
                total, mn, mx, p25, p50, p75 = row
                return {
                    "total_rows": int(total),
                    "min_val":    _fmt_num(mn),
                    "max_val":    _fmt_num(mx),
                    "p25":        _fmt_num(p25),
                    "p50":        _fmt_num(p50),
                    "p75":        _fmt_num(p75),
                }
        except Exception as e1:
            logger.debug("Percentile stats failed for %s: %s", col_name, e1)

        # Attempt 2: simple MIN / MAX / COUNT (no percentiles)
        try:
            cursor.execute(f"""
                SELECT COUNT(*), MIN({col_name}), MAX({col_name})
                FROM ({safe_sql}) _stats_src
                WHERE {col_name} IS NOT NULL
            """)
            row = cursor.fetchone()
            if row and row[0]:
                total, mn, mx = row
                return {
                    "total_rows": int(total),
                    "min_val":    _fmt_num(mn),
                    "max_val":    _fmt_num(mx),
                    "p25":        None,
                    "p50":        None,
                    "p75":        None,
                }
        except Exception as e2:
            logger.debug("MIN/MAX fallback failed for %s: %s", col_name, e2)

        return None
    finally:
        cursor.close()


def _fmt_num(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except Exception:
        return v


# ──────────────────────────────────────────────────────────────────────────────
# THRESHOLD SUGGESTION ENGINE
# ──────────────────────────────────────────────────────────────────────────────

_RANGE_PATTERN = re.compile(
    r'\b(?:\w+\.)?(\w+)\s*(>=|<=|>|<)\s*(@[\w_]+|[\d.]+)',
    re.IGNORECASE
)
_EQ_PATTERN = re.compile(
    r"\b(?:\w+\.)?(\w+)\s*=\s*'?([^'\s,)]+)'?",
    re.IGNORECASE
)


_DATE_PARAM_WORDS = ("DATE", "BUSINESS_DATE", "BATCH_DATE", "CURRENT_DATE")


def _extract_range_items(
    condition: str,
    threshold_bindings: dict = None,
    param_mapping: dict = None,
) -> list[dict]:
    """
    Extracts simple column range/equality comparisons from a condition string.
    Returns list of {col, op, val, param_name} dicts for numeric comparisons only.
    Handles both numeric literals (>= 9) and @ParamName references (>= @Efctv_Risk_Lvl).

    param_mapping: {col_lower: {op: param_name}} built from the main SQL — used to
    resolve param names for conditions that use literal values in the dataset SQL.
    """
    items = []
    for m in _RANGE_PATTERN.finditer(condition):
        col = m.group(1)
        if any(s in col.upper() for s in ["_FL", "_DT", "DATE", "SYSDATE"]):
            continue
        raw = m.group(3)
        if raw.startswith("@"):
            param_name = raw[1:]
            # Skip date-type params — they're not numeric thresholds
            if any(d in param_name.upper() for d in _DATE_PARAM_WORDS):
                continue
            # Look up numeric value from KDD_TSHLD_BINDING if available
            val = None
            if threshold_bindings:
                bound = threshold_bindings.get(param_name)
                if bound is not None:
                    try:
                        val = float(bound)
                    except (ValueError, TypeError):
                        pass
            items.append({"col": col, "op": m.group(2), "val": val, "param_name": param_name})
        else:
            try:
                val = float(raw)
            except ValueError:
                continue
            # Skip trivially non-restrictive conditions (>= 0, > -1, etc.)
            if m.group(2) in (">=", ">") and val <= 0:
                continue
            # Resolve param_name from main SQL mapping (dataset SQL uses literal values)
            param_name = None
            if param_mapping:
                param_name = (param_mapping.get(col.lower()) or {}).get(m.group(2))
            items.append({"col": col, "op": m.group(2), "val": val, "param_name": param_name})
    return items


_INEQ_PATTERN = re.compile(
    r"\b(?:\w+\.)?(\w+)\s*(<>|!=|=)\s*'([^']*)'", re.IGNORECASE
)
_IN_PATTERN = re.compile(
    r"\b(?:\w+\.)?(\w+)\s+(NOT\s+IN|IN)\s*\(\s*((?:'[^']*'\s*,?\s*)+)\)", re.IGNORECASE
)


def _value_satisfies_condition(op: str, expected, actual) -> bool:
    """Evaluates whether an observed column value would satisfy the given
    equality/inequality/IN condition — computed here, in code, rather than
    left for the AI to reason about. Confirmed live that a local 8B model can
    get condition polarity backwards even when given the raw facts (e.g.
    claiming a value has "no data" when the facts explicitly show it does)."""
    if op == "=":
        return actual == expected
    if op in ("<>", "!="):
        return actual != expected
    if op == "IN":
        return actual in (expected or [])
    if op == "NOT IN":
        return actual not in (expected or [])
    return False


def _extract_equality_terms(condition: str) -> dict | None:
    """
    Extracts a single {col, op, value} from a simple equality/inequality/IN
    condition on a string/flag literal (e.g. col = 'Y', col <> 'Y',
    col IN ('A','B')) — the non-numeric counterpart to _extract_range_items.

    Returns None for anything else (cross-table equality, numeric comparison,
    multi-condition strings, or no match) rather than guessing.
    """
    condition = condition.strip()

    in_m = _IN_PATTERN.search(condition)
    if in_m:
        col, op, values_raw = in_m.group(1), in_m.group(2).upper(), in_m.group(3)
        values = [v.strip().strip("'") for v in values_raw.split(",") if v.strip()]
        return {"col": col, "op": op, "value": values}

    eq_m = _INEQ_PATTERN.search(condition)
    if eq_m:
        col, op, value = eq_m.group(1), eq_m.group(2), eq_m.group(3)
        # Skip cross-table equality (alias.col = alias2.col2) — no literal here
        if re.search(r"=\s*\w+\.\w+", condition):
            return None
        return {"col": col, "op": op, "value": value}

    return None


def suggest_thresholds(
    conn,
    condition:          str,
    preceding_sql:      str,
    metadata:           dict,
    threshold_bindings: dict,
    threshold_config:   dict = None,
    full_cte_sql:       str = None,
    param_mapping:      dict = None,
) -> list[dict]:
    """
    For a failing condition, extracts all numeric range comparisons, queries
    actual data distributions, and returns threshold suggestions.

    full_cte_sql: the complete CTE SQL (all conditions intact) — used by the
    substitution-based simulator which replaces the threshold value directly.
    param_mapping: {col_lower: {op: param_name}} from the main SQL — used to
    resolve KDD_TSHLD param names when the dataset SQL uses literal values.

    Returns list of suggestion dicts.
    """
    items = _extract_range_items(condition, threshold_bindings, param_mapping)
    if not items:
        return []

    # Group by column name → find the range pair (min op, max op)
    by_col: dict[str, dict] = {}
    for item in items:
        col = item["col"].upper()
        if col not in by_col:
            by_col[col] = {"col": item["col"], "ranges": []}
        by_col[col]["ranges"].append(item)

    suggestions = []
    for col_upper, info in by_col.items():
        col = info["col"]
        ranges = info["ranges"]

        # Determine the current threshold bounds
        current_min = None
        current_max = None
        min_param   = None
        max_param   = None
        for r in ranges:
            if r["op"] in (">=", ">"):
                current_min = r["val"]
                # Prefer inline @ParamName over KDD_TSHLD_BINDING reverse lookup
                min_param = r.get("param_name") or _match_param_name(r["val"], threshold_bindings)
            elif r["op"] in ("<=", "<"):
                current_max = r["val"]
                max_param = r.get("param_name") or _match_param_name(r["val"], threshold_bindings)

        # KDD_TSHLD configured bounds for this parameter (Approach 1 candidates)
        param_key   = min_param or max_param
        tshld_entry = (threshold_config or {}).get(param_key) if param_key else None
        kdd_min_val = None
        kdd_max_val = None
        if tshld_entry:
            try:
                kdd_min_val = float(tshld_entry["min"]) if tshld_entry.get("min") is not None else None
                kdd_max_val = float(tshld_entry["max"]) if tshld_entry.get("max") is not None else None
            except (ValueError, TypeError):
                pass

        # Query actual stats from the data available BEFORE this condition
        stats = _query_column_stats(conn, col, preceding_sql, metadata, threshold_bindings)

        # Simulate: substitute values directly in full_cte_sql (primary),
        # fall back to inject/wrap using preceding_sql (legacy).
        sim_min = _simulate_threshold(
            conn, col, ">=", current_min, preceding_sql, metadata, threshold_bindings,
            full_cte_sql=full_cte_sql, kdd_candidate=kdd_min_val
        ) if current_min is not None else None
        sim_max = _simulate_threshold(
            conn, col, "<=", current_max, preceding_sql, metadata, threshold_bindings,
            full_cte_sql=full_cte_sql, kdd_candidate=kdd_max_val
        ) if current_max is not None else None

        # Always look up KDD_TSHLD entry regardless of whether live stats succeeded
        param_key   = min_param or max_param
        tshld_entry = (threshold_config or {}).get(param_key) if param_key else None

        suggestion = {
            "column":       col,
            "current_min":  current_min,
            "current_max":  current_max,
            "param_min":    min_param,
            "param_max":    max_param,
            "sim_min":      sim_min,
            "sim_max":      sim_max,
            "tshld_min":    tshld_entry.get("min")  if tshld_entry else None,
            "tshld_max":    tshld_entry.get("max")  if tshld_entry else None,
            "tshld_curr":   tshld_entry.get("curr") if tshld_entry else None,
        }

        if stats:
            suggestion.update({
                "data_total_rows": stats["total_rows"],
                "data_min":        stats["min_val"],
                "data_max":        stats["max_val"],
                "data_p25":        stats["p25"],
                "data_p75":        stats["p75"],
            })
            suggestion["suggestion"] = _build_suggestion_text(
                col, current_min, current_max, stats, min_param, max_param, sim_min, sim_max
            )
        else:
            hint_parts = []
            if current_min is not None:
                hint_parts.append(f"Minimum threshold: {_n(current_min)}")
            if current_max is not None:
                hint_parts.append(f"Maximum threshold: {_n(current_max)}")
            _append_simulation_lines(hint_parts, min_param, max_param, sim_min, sim_max)
            suggestion["suggestion"] = "\n".join(hint_parts) if hint_parts else (
                f"Could not query the actual distribution of '{col}' from the database."
            )

        suggestions.append(suggestion)

    return suggestions


def _build_suggestion_text(
    col, current_min, current_max, stats, min_param, max_param,
    sim_min=None, sim_max=None
) -> str:
    data_min  = stats.get("min_val")
    data_max  = stats.get("max_val")
    rows      = stats.get("total_rows", 0)
    p25       = stats.get("p25")
    p75       = stats.get("p75")

    if data_min is None and data_max is None:
        return f"No non-null data found for '{col}' — column may be empty in the base dataset."

    parts = [
        f"Without the threshold filter, '{col}' has {rows:,} rows with values "
        f"ranging from {_n(data_min)} to {_n(data_max)}."
    ]
    if p25 is not None and p75 is not None:
        parts.append(f"  P25 = {_n(p25)}   P75 = {_n(p75)}")

    # Minimum threshold too high
    if current_min is not None and data_max is not None and float(data_max) < float(current_min):
        min_label = f"@{min_param}" if min_param else "the minimum threshold"
        parts.append(
            f"  ❌ Current minimum ({_n(current_min)}) is above all data (max = {_n(data_max)}). "
            f"Set {min_label} = {_n(data_min)} to include all {rows:,} rows."
        )

    # Maximum threshold too low
    if current_max is not None and data_min is not None and float(data_min) > float(current_max):
        max_label = f"@{max_param}" if max_param else "the maximum threshold"
        parts.append(
            f"  ❌ Current maximum ({_n(current_max)}) is below all data (min = {_n(data_min)}). "
            f"Set {max_label} = {_n(data_max)} to include all {rows:,} rows."
        )

    # Both set, combined range excludes everything
    if current_min is not None and current_max is not None:
        if not (
            (data_max is not None and float(data_max) < float(current_min)) or
            (data_min is not None and float(data_min) > float(current_max))
        ):
            min_label = f"@{min_param}" if min_param else "the minimum"
            max_label = f"@{max_param}" if max_param else "the maximum"
            parts.append(
                f"  ⚠ Combined range [{_n(current_min)}, {_n(current_max)}] excludes all data. "
                f"Set {min_label} = {_n(data_min)} and {max_label} = {_n(data_max)} "
                f"to include all {rows:,} rows."
            )

    _append_simulation_lines(parts, min_param, max_param, sim_min, sim_max)
    return "\n".join(parts)


def _append_simulation_lines(parts: list, min_param, max_param, sim_min, sim_max):
    """Append simulation findings (if any) to a suggestion text parts list."""
    for sim, param, op_label in (
        (sim_min, min_param, "minimum"),
        (sim_max, max_param, "maximum"),
    ):
        if not sim:
            continue
        found = sim.get("found")
        label = f"@{param}" if param else f"the {op_label} threshold"
        if found:
            parts.append(
                f"\n  Simulated: setting {label} = {_n(found['val'])} "
                f"returns {found['count']:,} row(s). Use this value."
            )
        else:
            tried = sim.get("attempts", [])
            if tried:
                summary = ", ".join(
                    f"{_n(a['val'])} → {a['count']:,}" for a in tried
                )
                parts.append(
                    f"\n  Simulated values tried for {label}: {summary}. None returned rows."
                )


def _n(v) -> str:
    if v is None:
        return "N/A"
    try:
        f = float(v)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except Exception:
        return str(v)


# ──────────────────────────────────────────────────────────────────────────────
# OR-BLOCK DRILL-DOWN
# ──────────────────────────────────────────────────────────────────────────────

def _drill_or_block(
    conn, condition: str, base_sql: str, metadata: dict,
    threshold_bindings: dict, threshold_config: dict = None,
    full_cte_sql: str = None, param_mapping: dict = None,
    run_logger=None
) -> dict | None:
    """
    When a failing WHERE/HAVING condition is a big OR block, splits it by OR
    and tests each branch individually.  For branches that fail, tries to
    identify the sub-condition responsible and suggest thresholds.
    Returns an enriched result dict or None.
    """
    branches = _split_or_conditions(condition)
    if len(branches) <= 1:
        return None

    print(f"\n  OR block has {len(branches)} branches — testing each individually...")

    failing_branches = []
    for idx, branch in enumerate(branches):
        branch_sql = base_sql + f"\nWHERE ({branch})"
        count = execute_count(conn, branch_sql, metadata, run_logger=run_logger, step=6, label=f"or_branch_{idx+1}")
        status = "PASS" if count > 0 else "FAIL"
        print(f"    Branch {idx + 1}: {status} ({count:,} rows)  {branch[:80].strip()}...")
        if count == 0:
            failing_branches.append({"branch_idx": idx + 1, "branch": branch})

    if not failing_branches:
        return None

    # Analyse the first failing branch for threshold suggestions
    first_fail = failing_branches[0]
    branch_text = first_fail["branch"]

    # Try to split the branch by AND and find the sub-condition that kills rows
    sub_conditions = _unwrap_or_split_and(branch_text)
    killing_sub = None
    preceding = base_sql
    prev_count = execute_count(conn, base_sql, metadata, run_logger=run_logger, step=6, label="or_block_base_sql")

    if len(sub_conditions) > 1:
        sub_where = ""
        for i, sub in enumerate(sub_conditions):
            sub_where += ("\nWHERE " if i == 0 else "\n  AND ") + sub
            cur = execute_count(conn, base_sql + sub_where, metadata, run_logger=run_logger, step=6, label=f"or_subcond_{i+1}")
            print(f"    Sub-condition {i + 1}: {prev_count:,} → {cur:,}  {sub[:80].strip()}")
            if prev_count > 0 and cur == 0:
                killing_sub = sub
                preceding   = base_sql + sub_where.replace(
                    ("\nWHERE " if i == 0 else "\n  AND ") + sub, ""
                )
                break
            prev_count = cur

    target_condition = killing_sub or branch_text

    # If the detected killer has no numeric range items (e.g. Overall_Risk = 'HR' is a
    # categorical equality that simply has no matching data), fall back to analysing the
    # full branch text so we surface the numeric thresholds buried deeper in the branch.
    # preceding is already base_sql when the categorical condition is the first sub-cond,
    # so column stats will still reflect the pre-filter data set.
    numeric_items = _extract_range_items(target_condition, threshold_bindings, param_mapping)
    if not numeric_items and killing_sub:
        target_condition = branch_text
        preceding        = base_sql   # stats should run against the unfiltered base
        print("    Killing sub-condition has no numeric thresholds — analysing full branch for suggestions")

    threshold_suggestions = suggest_thresholds(
        conn, target_condition, preceding, metadata, threshold_bindings, threshold_config,
        full_cte_sql=full_cte_sql, param_mapping=param_mapping
    )

    # If still no suggestions (all branches fail for categorical reasons), iterate
    # through remaining failing branches until we find one with numeric suggestions.
    if not threshold_suggestions:
        for fail in failing_branches[1:]:
            alt_branch = fail["branch"]
            alt_sug = suggest_thresholds(
                conn, alt_branch, base_sql, metadata, threshold_bindings, threshold_config,
                full_cte_sql=full_cte_sql, param_mapping=param_mapping
            )
            if alt_sug:
                threshold_suggestions = alt_sug
                target_condition      = alt_branch
                print(f"    Using branch {fail['branch_idx']} for threshold suggestions")
                break

    return {
        "or_branch_count":     len(branches),
        "failing_branches":    [b["branch_idx"] for b in failing_branches],
        "killing_condition":   target_condition,
        "threshold_suggestions": threshold_suggestions,
    }


# ──────────────────────────────────────────────────────────────────────────────
# INNER SUBQUERY ANALYSIS  (for UNKNOWN cases)
# ──────────────────────────────────────────────────────────────────────────────

def _find_inner_having_sql(sql: str) -> tuple[str | None, str | None]:
    """
    For UNKNOWN cases where the outer WHERE hasn't fired, tries to locate the
    innermost HAVING block at depth > 0 and returns (sql_without_having, having_block_text).
    Call this on base_sql (no outer WHERE), not the full SQL.
    """
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

    # Find the last HAVING that appears inside the query (depth > 0)
    pattern = re.compile(r'\bHAVING\b', re.IGNORECASE)
    last_having_pos   = -1
    last_having_depth = -1
    for m in pattern.finditer(clean):
        depth = clean[:m.start()].count('(') - clean[:m.start()].count(')')
        if depth > 0:
            last_having_pos   = m.start()
            last_having_depth = depth

    if last_having_pos == -1:
        return None, None

    # Find end of the HAVING block: scan until we close the surrounding parenthesis
    after = clean[last_having_pos + 7:]
    depth = last_having_depth
    end_pos = len(after)
    for i, ch in enumerate(after):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < last_having_depth:
                end_pos = i
                break

    having_text = after[:end_pos].strip()
    sql_without = clean[:last_having_pos].rstrip() + "\n" + clean[last_having_pos + 7 + end_pos:]

    return sql_without, having_text


def _inject_having(sql_without_having: str, having_clause: str) -> str:
    """
    Re-inserts a HAVING clause just before the outermost closing ')' in
    sql_without_having (the ')' that takes depth from 1 → 0).

    having_clause should be the full string, e.g. "HAVING cond1 AND cond2".
    """
    depth = 0
    insert_before = -1
    for i, ch in enumerate(sql_without_having):
        if ch == '(':
            depth += 1
        elif ch == ')':
            if depth == 1:        # this ) closes the outermost subquery
                insert_before = i
            depth -= 1
    if insert_before == -1:
        return sql_without_having.rstrip() + "\n" + having_clause
    return (
        sql_without_having[:insert_before].rstrip()
        + "\n" + having_clause
        + "\n" + sql_without_having[insert_before:]
    )


def _analyze_branch_failure(conn, branch_sql: str, metadata: dict, run_logger=None, output_dir: str = "") -> dict:
    """
    For a zero-count UNION ALL branch, explains WHY it returns 0 rows:
      1. Counts each source table individually
      2. If all sources have data, progressively adds WHERE conditions to find killer
    """
    clean = re.sub(r'/\*.*?\*/', '', branch_sql, flags=re.DOTALL).strip()

    from_pos  = _find_top_level_clause("FROM",  clean)
    where_pos = _find_top_level_clause("WHERE", clean)

    if from_pos == -1:
        return {"error": "Cannot parse FROM clause"}

    select_part = clean[:from_pos].rstrip()
    from_part   = clean[from_pos:where_pos].rstrip() if where_pos != -1 else clean[from_pos:]
    where_part  = clean[where_pos + 5:].strip()      if where_pos != -1 else None

    # Extract source table/view names — supports both syntaxes:
    #   Comma-join (LRT):  FROM Wire_Trxn_Vw tr, Cust_Accounts ca
    #   Explicit join (RMF): FROM wire_vw w INNER JOIN fccmatomic.ACCT a ON ...
    from_body = from_part[4:].strip()   # strip leading "FROM"

    all_source_names: list[str] = []
    seen_upper: set[str] = set()

    # 1) Comma-split the FROM list up to the first JOIN keyword
    first_join_m = re.search(r'\bJOIN\b', from_body, re.IGNORECASE)
    from_list_end = first_join_m.start() if first_join_m else len(from_body)
    from_list_text = from_body[:from_list_end]

    depth_c, buf = 0, ""
    for ch in from_list_text:
        if ch == '(':
            depth_c += 1; buf += ch
        elif ch == ')':
            depth_c -= 1; buf += ch
        elif ch == ',' and depth_c == 0:
            parts = buf.strip().split()
            if parts:
                name = parts[0]
                if name.upper() not in seen_upper:
                    seen_upper.add(name.upper()); all_source_names.append(name)
            buf = ""
        else:
            buf += ch
    parts = buf.strip().split()
    if parts:
        name = parts[0]
        if name.upper() not in seen_upper:
            seen_upper.add(name.upper()); all_source_names.append(name)

    # 2) Pick up tables from JOIN ... ON clauses
    for m in re.finditer(r'\bJOIN\s+([\w.]+)', from_body, re.IGNORECASE):
        name = m.group(1)
        if name.upper() not in seen_upper:
            seen_upper.add(name.upper()); all_source_names.append(name)

    result: dict = {"source_counts": []}

    # Count each source table/view independently
    all_have_data = True
    for name in all_source_names:
        cnt   = None
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {name}")
            cnt = cursor.fetchone()[0]
        except Exception as e:
            logger.debug("branch source count %s: %s", name, e)
        finally:
            cursor.close()
        result["source_counts"].append({"table": name, "row_count": cnt})
        if cnt == 0:
            all_have_data = False

    if not all_have_data:
        empty = [s["table"] for s in result["source_counts"] if s.get("row_count") == 0]
        result["issue"]       = "no_source_data"
        result["explanation"] = f"Source table(s) have 0 rows: {', '.join(empty)}"
        return result

    # All sources have rows → WHERE conditions are killing the join
    if not where_part:
        result["issue"]       = "unknown"
        result["explanation"] = "Sources have data but no WHERE clause — unexpected."
        return result

    base_from_sql = select_part + "\n" + from_part
    conditions    = _split_and_conditions(where_part)
    where_analysis: list[dict] = []

    if len(conditions) <= 1:
        # Single-condition WHERE (possibly parens-wrapped join condition) — report it directly
        result["issue"]                   = "where_condition"
        result["killing_where_condition"] = conditions[0].strip() if conditions else where_part.strip()
        result["explanation"]             = (
            "JOIN/WHERE condition returns 0 rows — no matching keys across the source tables."
        )
        where_analysis.append({
            "condition":  result["killing_where_condition"],
            "rows_after": 0,
            "kills_rows": True,
        })
        source_check = _verify_killer_source(conn, result["killing_where_condition"], from_part, metadata)
        if source_check:
            result["source_check"] = source_check
    else:
        # Multiple AND conditions: try removing each one to find the killer.
        # Avoids an expensive Cartesian-product COUNT (no-WHERE base query).
        for i, cond in enumerate(conditions):
            remaining = [c for j, c in enumerate(conditions) if j != i]
            test_sql  = base_from_sql + "\nWHERE " + " AND ".join(remaining)
            cur       = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"branch_failure_remove_cond_{i}")
            entry     = {
                "condition":                cond.strip(),
                "rows_without_this_cond":   cur,
                "kills_rows":               cur > 0,
            }
            where_analysis.append(entry)
            if cur > 0 and "killing_where_condition" not in result:
                result["issue"]                   = "where_condition"
                result["killing_where_condition"] = cond.strip()
                result["explanation"]             = (
                    f"WHERE condition eliminates all rows: {cond.strip()[:150]}"
                )
                result["killing_condition_index"] = i
                result["rows_without_killer"] = cur
                source_check = _verify_killer_source(conn, cond.strip(), from_part, metadata)
                if source_check:
                    result["source_check"] = source_check

        if "issue" not in result:
            # All conditions individually appear necessary — combination is too restrictive
            result["issue"]       = "where_condition"
            result["explanation"] = (
                "All WHERE conditions individually are necessary; "
                "the combined JOIN is too restrictive — no rows survive all filters."
            )

    result["where_analysis"] = where_analysis

    # VERIFICATION: If a killing condition was found, verify by commenting it out in resolved SQL
    if result.get("killing_where_condition") and output_dir:
        resolved_sql_file = ""
        extracted_dir = os.path.join(output_dir, "extracted_queries")
        if os.path.isdir(extracted_dir):
            dataset_files = [
                os.path.join(extracted_dir, f)
                for f in os.listdir(extracted_dir)
                if "resolved_function_dataset" in f.lower() and f.lower().endswith(".sql")
            ]
            if dataset_files:
                resolved_sql_file = max(dataset_files, key=os.path.getmtime)

        if resolved_sql_file:
            killer = result["killing_where_condition"]
            if run_logger:
                run_logger.log(6, f"VERIFICATION: Loading resolved SQL from {os.path.basename(resolved_sql_file)}")
                run_logger.log(6, f"VERIFICATION: Commenting out: {killer[:120]}...")
            verification = verify_killer_condition(
                conn, killer, resolved_sql_file, metadata,
                run_logger=run_logger, output_dir=output_dir
            )
            result["verification"] = verification
            if run_logger:
                run_logger.log(6, f"VERIFICATION RESULT: {verification['message']}")
            if verification.get("verified"):
                result["explanation"] = (
                    f"✅ VERIFIED: Commenting out '{killer[:80]}...' generates {verification['rows']:,} alerts. "
                    f"This condition is the root cause."
                )
            else:
                result["explanation"] = (
                    f"⚠️ KILLER CONDITION FOUND (not verified): '{killer[:80]}...' "
                    f"Removing this condition restores {result.get('rows_without_killer', 0):,} rows. "
                    f"Verification failed: {verification.get('message', 'Could not locate in resolved SQL')}"
                )

    return result


def _resolve_from_table(remainder: str, max_depth: int = 4) -> str:
    """Given the text immediately following a FROM keyword (with 'FROM'
    itself already stripped), resolves the actual table/view name. A FROM
    clause opening with "(" is ambiguous between two real shapes seen live
    in OFSAA's final-query decomposition output:
      - a derived-table subquery: FROM (SELECT ... FROM <real tables> ...) —
        recurse into its own top-level FROM.
      - Oracle's parenthesized ANSI join-chain syntax: FROM (table1 JOIN
        table2 ON(...) JOIN table3 ON(...) ...) — the real table name is
        the very next identifier, no FROM keyword involved.
    Confirmed live: a 9-branch UNION ALL used the join-chain shape for 8 of
    its 9 branches, all silently reported "?" before this. Falls back to "?"
    if no resolvable table name is found within max_depth levels."""
    for _ in range(max_depth):
        stripped = None
        for ln in remainder.split('\n'):
            s = ln.strip()
            if not s or s.startswith('--'):
                continue
            stripped = s
            break
        if stripped is None:
            return "?"

        if stripped.startswith('('):
            open_pos = remainder.index('(')
            depth = 0
            close_pos = -1
            for i in range(open_pos, len(remainder)):
                if remainder[i] == '(':
                    depth += 1
                elif remainder[i] == ')':
                    depth -= 1
                    if depth == 0:
                        close_pos = i
                        break
            if close_pos == -1:
                return "?"
            inner = remainder[open_pos + 1:close_pos]
            inner_stripped = inner.strip()
            if re.match(r'SELECT\b', inner_stripped, re.IGNORECASE):
                inner_from_pos = _find_top_level_clause("FROM", inner)
                if inner_from_pos == -1:
                    return "?"
                remainder = re.sub(r'^FROM\s*', '', inner[inner_from_pos:].strip(), count=1, flags=re.IGNORECASE)
            else:
                # Parenthesized join-chain — the table name is the very
                # next identifier, not behind a nested FROM.
                remainder = inner
            continue

        m = re.match(r'([\w.]+)', stripped)
        return m.group(1) if m else "?"

    return "?"


def _probe_union_all_branches(conn, sql: str, metadata: dict, run_logger=None, output_dir: str = "") -> list[dict]:
    """
    Finds the UNION ALL block (at any nesting depth), counts rows per branch,
    and for zero-count branches calls _analyze_branch_failure.
    Returns [{branch_num, from_table, row_count, preview, failure_analysis?}].

    Strategy: find the first UNION ALL keyword, determine its depth, then
    scan backward/forward to locate the enclosing ( ... ) block.
    This is robust regardless of how many outer SELECT levels exist.
    """
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

    # Find first UNION ALL and its nesting depth
    union_m = re.search(r'\bUNION\s+ALL\b', clean, re.IGNORECASE)
    if not union_m:
        return []

    ua_pos   = union_m.start()
    ua_depth = clean[:ua_pos].count('(') - clean[:ua_pos].count(')')

    # Scan backward from ua_pos to find the ( that opened this block
    block_start = -1
    d = ua_depth
    for i in range(ua_pos - 1, -1, -1):
        ch = clean[i]
        if ch == ')':
            d += 1
        elif ch == '(':
            d -= 1
            if d == ua_depth - 1:
                block_start = i + 1
                break

    # Scan forward from ua_pos to find the matching closing )
    block_end = -1
    d = ua_depth
    for i in range(ua_pos, len(clean)):
        ch = clean[i]
        if ch == '(':
            d += 1
        elif ch == ')':
            d -= 1
            if d == ua_depth - 1:
                block_end = i
                break

    if block_start == -1 or block_end == -1:
        return []

    inner = clean[block_start:block_end]
    branches = _split_conditions_by(inner, "UNION ALL")

    results = []
    for idx, branch in enumerate(branches):
        branch = branch.strip()
        if not branch:
            continue
        count = execute_count(conn, branch, metadata, run_logger=run_logger, step=6, label=f"union_branch_{idx+1}")
        # Extract first meaningful -- comment (skip pure separator lines like "-- -------...")
        branch_label = None
        for _line in branch.split('\n'):
            _s = _line.strip()
            if _s.startswith('--'):
                _content = _s[2:].strip()
                if _content and not re.match(r'^[-=*\s]+$', _content):
                    branch_label = _content
                    break
        # Use top-level FROM position to avoid capturing subquery FROMs
        from_pos = _find_top_level_clause("FROM", branch)
        from_table = "?"
        if from_pos != -1:
            after_from = branch[from_pos:].strip()
            remainder = re.sub(r'^FROM\s*', '', after_from, count=1, flags=re.IGNORECASE)
            from_table = _resolve_from_table(remainder)
        entry = {
            "branch_num":   idx + 1,
            "branch_label": branch_label,
            "from_table":   from_table,
            "row_count":    count,
            "preview":      branch[:200].replace('\n', ' ').strip(),
        }
        if count == 0:
            entry["failure_analysis"] = _analyze_branch_failure(conn, branch, metadata, run_logger=run_logger, output_dir=output_dir)
        results.append(entry)
    return results


def _probe_source_views(conn, sql: str, metadata: dict) -> list[dict]:
    """
    Finds tables/views referenced in FROM/JOIN (at any depth) and probes their
    row counts.  For clndr_vw, also shows the computed date range columns.
    """
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    pattern = re.compile(r'\b(?:FROM|JOIN)\s+([\w]+(?:\.[\w]+)?)', re.IGNORECASE)
    seen: set[str] = set()
    names: list[str] = []
    for m in pattern.finditer(clean):
        name = m.group(1)
        key  = name.upper()
        if key in seen or key == "DUAL":
            continue
        seen.add(key)
        names.append(name)

    results = []
    for name in names:
        info: dict = {"name": name}
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {name}")
            info["row_count"] = cursor.fetchone()[0]
        except Exception as e:
            info["row_count"] = None
            info["error"]     = str(e)[:120]
        finally:
            cursor.close()

        if "CLNDR" in name.upper():
            cursor = conn.cursor()
            try:
                cursor.execute(f"SELECT * FROM {name} FETCH FIRST 1 ROWS ONLY")
                row = cursor.fetchone()
                if row:
                    cols = [d[0].lower() for d in cursor.description]
                    info["clndr_data"] = {
                        col: (str(val)[:10] if val is not None else None)
                        for col, val in zip(cols, row)
                    }
            except Exception:
                pass
            finally:
                cursor.close()

        results.append(info)
    return results


def _investigate_deeper_failure(
    conn, resolved_sql_file: str, fixed_cte_name: str, metadata: dict,
    run_logger=None
) -> dict:
    """
    Called when replacing a CTE's WHERE clause with 1=1 still produces 0 alerts.
    Investigates the final query (the outer SELECT after the WITH clause) to
    find which WHERE/HAVING condition in the final query is killing rows.

    Returns a dict with:
      - explanation: human-readable finding
      - failure_condition: the specific condition that kills rows (or None)
      - condition_line_number: line number in the dataset query (or None)
    """
    result = {"explanation": "", "failure_condition": None, "condition_line_number": None}

    if not os.path.exists(resolved_sql_file):
        result["explanation"] = "Could not load resolved SQL for deeper investigation."
        return result

    with open(resolved_sql_file, "r", encoding="utf-8") as f:
        full_sql = f.read()

    # Step 1: Create modified SQL with the fixed CTE's WHERE replaced
    modified_sql, err = _replace_cte_where_clause(full_sql, fixed_cte_name)
    if modified_sql is None:
        result["explanation"] = f"Could not replace WHERE clause: {err}"
        return result

    if run_logger:
        run_logger.log(6, f"DEEPER INVESTIGATION: Replaced CTE '{fixed_cte_name}' WHERE with 1=1, now diagnosing the final query...")

    # Step 2: Extract the final query — the part AFTER the WITH clause
    final_query_sql = _extract_final_query_from_with_clause(modified_sql)

    if not final_query_sql:
        # Try loading from parsed_ctes/final_query.sql (saved by CTE parser step)
        output_dir_path = os.path.dirname(os.path.dirname(resolved_sql_file))
        final_query_file = os.path.join(output_dir_path, "parsed_ctes", "final_query.sql")
        if os.path.exists(final_query_file):
            with open(final_query_file, "r", encoding="utf-8") as f:
                final_query_sql = f.read()
            if run_logger:
                run_logger.log(6, f"DEEPER INVESTIGATION: Loaded final query from parsed_ctes/final_query.sql ({len(final_query_sql)} chars)")
        else:
            # If no final_query.sql, the entire SQL is the final query
            final_query_sql = modified_sql
            if run_logger:
                run_logger.log(6, f"DEEPER INVESTIGATION: No final_query.sql found, using full SQL ({len(final_query_sql)} chars)")

    if run_logger and final_query_sql:
        run_logger.log(6, f"DEEPER INVESTIGATION: Final query ready ({len(final_query_sql)} chars)")

    # Step 3: Diagnose the final query's WHERE conditions
    try:
        parsed = parse_sql_structure(final_query_sql)
    except Exception as e:
        result["explanation"] = f"Could not parse final query structure: {e}"
        return result

    where_conditions = parsed.get("where_conditions", [])
    having_conditions = parsed.get("having_conditions", [])
    base_sql = parsed.get("from_with_joins_and_group") or parsed.get("from_with_joins", "")

    if not base_sql:
        result["explanation"] = "Could not extract base SQL from final query."
        return result

    # Check WHERE conditions — remove one at a time to find the killer
    if where_conditions and len(where_conditions) > 0:
        if run_logger:
            run_logger.log(6, f"DEEPER INVESTIGATION: Testing {len(where_conditions)} WHERE conditions in final query...")

        for i, cond in enumerate(where_conditions):
            remaining = [c for j, c in enumerate(where_conditions) if j != i]
            if remaining:
                test_sql = base_sql + "\nWHERE " + "\n  AND ".join(remaining)
            else:
                test_sql = base_sql
            cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"deeper_where_remove_cond_{i}")

            if cur > 0:
                # Found the killer condition!
                cond_stripped = cond.strip()
                result["failure_condition"] = cond_stripped
                result["explanation"] = (
                    f"Final query WHERE condition '{cond_stripped[:100]}' eliminates all rows "
                    f"(removing it restores {cur:,} rows). "
                    f"This is an ADDITIONAL issue beyond CTE '{fixed_cte_name}'."
                )

                # Find the line number in the resolved SQL
                line_num = _find_condition_line_in_sql(cond_stripped, full_sql)
                if line_num:
                    result["condition_line_number"] = line_num

                if run_logger:
                    run_logger.log(6, f"DEEPER INVESTIGATION: FOUND killer WHERE condition: {cond_stripped[:80]}...")
                    if line_num:
                        run_logger.log(6, f"DEEPER INVESTIGATION: Line number: {line_num}")

                return result

    # Check HAVING conditions — add one at a time to find the killer
    if having_conditions and len(having_conditions) > 0:
        if run_logger:
            run_logger.log(6, f"DEEPER INVESTIGATION: Testing {len(having_conditions)} HAVING conditions in final query...")

        before_having = parsed.get("before_having", final_query_sql)
        prev_count = execute_count(conn, before_having, metadata, run_logger=run_logger, step=6, label="deeper_having_before")

        having_so_far = ""
        for i, cond in enumerate(having_conditions):
            having_so_far += ("\nHAVING " if i == 0 else "\n  AND ") + cond
            test_sql = before_having + having_so_far
            cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"deeper_having_add_cond_{i}")

            if prev_count > 0 and cur == 0:
                cond_stripped = cond.strip()
                result["failure_condition"] = cond_stripped
                result["explanation"] = (
                    f"Final query HAVING condition '{cond_stripped[:100]}' eliminates all rows "
                    f"({prev_count:,} → 0). "
                    f"This is an ADDITIONAL issue beyond CTE '{fixed_cte_name}'."
                )

                line_num = _find_condition_line_in_sql(cond_stripped, full_sql)
                if line_num:
                    result["condition_line_number"] = line_num

                if run_logger:
                    run_logger.log(6, f"DEEPER INVESTIGATION: FOUND killer HAVING condition: {cond_stripped[:80]}...")
                    if line_num:
                        run_logger.log(6, f"DEEPER INVESTIGATION: Line number: {line_num}")

                return result

            prev_count = cur

    # No single condition found — check if the final query has a UNION ALL with empty branches
    union_branches = _probe_union_all_branches(conn, final_query_sql, metadata, run_logger=run_logger)
    if union_branches:
        empty_branches = [b for b in union_branches if b.get("row_count", 0) == 0]
        if empty_branches and all(b.get("row_count", 0) == 0 for b in union_branches):
            # All branches empty — no data in any source
            result["explanation"] = (
                f"All {len(union_branches)} UNION ALL branches in the final query return 0 rows. "
                f"The source tables may be empty for the current batch date."
            )
            result["union_branches"] = union_branches
            return result

    result["explanation"] = (
        "No single WHERE or HAVING condition in the final query is the killer. "
        "The issue may be a combination of conditions or a data availability problem."
    )
    return result


def _extract_final_query_from_with_clause(sql: str) -> str:
    """
    Extracts the final query from a 'WITH ... AS (...), ... <final_query>' SQL.
    Returns the final query SQL (everything after the last CTE definition).
    If there's no WITH clause, returns empty string.
    """
    # Strip block comments and leading line comments
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    lines = clean.split('\n')
    first_real = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('--'):
            first_real = i
            break
    clean = '\n'.join(lines[first_real:]).strip()

    # Check if it starts with WITH
    with_match = re.match(r'\s*WITH\b', clean, re.IGNORECASE)
    if not with_match:
        return ""

    # Find the end of the WITH clause — track paren depth to find the last
    # top-level closing paren of the last CTE definition
    depth = 0
    last_cte_close = -1
    i = with_match.end()

    while i < len(clean):
        ch = clean[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                last_cte_close = i
        i += 1

    if last_cte_close == -1:
        return ""

    # Everything after the last CTE's closing paren is the final query
    final_query = clean[last_cte_close + 1:].strip()

    # If the final query is very short (< 50 chars), it's likely not a real
    # final query — the SQL probably ends with the WITH clause and the final
    # query is a separate file. Return empty to signal that.
    if len(final_query) < 50:
        return ""

    return final_query


def _analyze_inner_subquery(
    conn, sql: str, metadata: dict, threshold_bindings: dict,
    threshold_config: dict = None, param_mapping: dict = None,
    run_logger=None, output_dir: str = "", cte_name: str = "",
    dataset_query_raw: str = None
) -> dict:
    """
    Called for UNKNOWN cases.  Tries multiple strategies to explain why the CTE
    returns 0 rows even though all intermediate CTEs passed.

    Returns a data_availability dict with findings.
    """
    findings: dict = {}

    # ── Strategy 1: Does the inner subquery return rows WITHOUT the outer WHERE? ──
    parsed   = parse_sql_structure(sql)
    base_sql = parsed.get("from_with_joins_and_group") or parsed["from_with_joins"]   # SELECT + FROM + GROUP BY, no outer WHERE

    if not base_sql:
        findings["issue"]       = "parse_failed"
        findings["explanation"] = (
            "Could not parse the SQL structure of this CTE. "
            "The query may be too complex for automated analysis."
        )
        return findings

    base_count = execute_count(conn, base_sql, metadata, run_logger=run_logger, step=6, label="inner_subquery_no_outer_where")
    findings["rows_without_outer_where"] = base_count

    if base_count > 0:
        # Inner subquery has rows — the outer WHERE combination kills them all
        findings["issue"]       = "where_combination"
        findings["explanation"] = (
            f"The inner query returns {base_count:,} rows before the outer WHERE filters. "
            "No single WHERE condition eliminates rows on its own, but the combined "
            "threshold conditions are too restrictive."
        )

        # Find line numbers of each WHERE condition in the dataset query
        where_conds = parsed.get("where_conditions", [])
        findings["where_conditions"] = where_conds

        condition_lines: list[dict] = []
        for cond in where_conds:
            cond_stripped = cond.strip()
            line_num = None
            if dataset_query_raw:
                line_num = _find_condition_line_in_sql(cond_stripped, dataset_query_raw)
            condition_lines.append({
                "condition": cond_stripped,
                "line_number": line_num,
            })
        if condition_lines:
            findings["where_condition_lines"] = condition_lines
            # Report the first condition's line as the primary line number
            first_line = condition_lines[0].get("line_number")
            if first_line:
                findings["first_condition_line"] = first_line

        # Collect actual data distributions for each numeric threshold column
        stats_list: list[dict] = []
        seen_cols: set[str] = set()
        for cond in where_conds:
            for item in _extract_range_items(cond, threshold_bindings):
                col = item["col"]
                if col.upper() in seen_cols:
                    continue
                seen_cols.add(col.upper())
                stats = _query_column_stats(conn, col, base_sql, metadata, threshold_bindings)
                if stats:
                    stats_list.append({"column": col, **stats})
        if stats_list:
            findings["column_stats"] = stats_list

        # VERIFICATION: Replace the CTE's WHERE clause with WHERE 1=1 in resolved SQL
        if output_dir:
            extracted_dir = os.path.join(output_dir, "extracted_queries")
            if os.path.isdir(extracted_dir):
                dataset_files = [
                    os.path.join(extracted_dir, f)
                    for f in os.listdir(extracted_dir)
                    if "resolved_function_dataset" in f.lower() and f.lower().endswith(".sql")
                ]
                if dataset_files:
                    resolved_sql_file = max(dataset_files, key=os.path.getmtime)
                    verification = _verify_where_combination(
                        conn, resolved_sql_file, findings["where_conditions"],
                        metadata, run_logger=run_logger, cte_name=cte_name
                    )
                    findings["verification"] = verification
                    if verification.get("error"):
                        findings["explanation"] = (
                            f"Inner query returns {base_count:,} rows before outer WHERE, "
                            f"but verification SQL failed with error: {verification['error']}. "
                            f"The combined WHERE conditions are likely the root cause."
                        )
                    elif verification.get("verified"):
                        findings["explanation"] = (
                            f"VERIFIED: Inner query returns {base_count:,} rows before outer WHERE. "
                            f"Replacing WHERE clause with 1=1 generates {verification['rows']:,} alerts. "
                            f"The combined WHERE conditions are the root cause."
                        )
                    elif verification.get("conditions_commented", 0) > 0:
                        # Verification ran but still 0 alerts — INVESTIGATE deeper
                        deeper = _investigate_deeper_failure(
                            conn, resolved_sql_file, cte_name, metadata,
                            run_logger=run_logger
                        )
                        if deeper:
                            findings["deeper_investigation"] = deeper
                            findings["explanation"] = (
                                f"Inner query returns {base_count:,} rows before outer WHERE. "
                                f"Replacing CTE '{cte_name}' WHERE with 1=1 still produces 0 alerts. "
                                f"Deeper investigation found: {deeper.get('explanation', '')}"
                            )
                            if deeper.get("failure_condition"):
                                findings["deeper_failure_condition"] = deeper["failure_condition"]
                            if deeper.get("condition_line_number"):
                                findings["deeper_condition_line"] = deeper["condition_line_number"]
                        else:
                            findings["explanation"] = (
                                f"Inner query returns {base_count:,} rows before outer WHERE. "
                                f"Replaced WHERE clause with 1=1 but still 0 alerts — "
                                f"check the final query or other CTEs for additional filtering."
                            )
                    else:
                        findings["explanation"] = (
                            f"Inner query returns {base_count:,} rows before outer WHERE. "
                            f"Could not replace WHERE clause in resolved SQL for verification. "
                            f"Manually comment out WHERE conditions to confirm root cause."
                        )
        return findings

    # ── base_count == 0: problem is INSIDE the inner subquery ────────────────

    # ── Strategy 2: Strip the inner HAVING from base_sql (not from full sql!) ──
    # BUG FIX: previously called _find_inner_having_sql(sql) which left the outer
    # WHERE intact, so "no_having_count" was still 0 for threshold reasons.
    base_no_having, having_text = _find_inner_having_sql(base_sql)

    if base_no_having and having_text:
        no_having_count = execute_count(conn, base_no_having, metadata, run_logger=run_logger, step=6, label="inner_subquery_no_having")
        findings["rows_without_inner_having"] = no_having_count

        if no_having_count > 0:
            # HAVING is definitely the culprit
            findings["issue"]                   = "inner_having"
            findings["inner_having_text"]       = having_text.strip()
            findings["inner_having_is_culprit"] = True
            findings["explanation"] = (
                f"Removing the inner HAVING clause restores {no_having_count:,} rows. "
                "The HAVING filter is eliminating all aggregated account groups."
            )

            # Progressive HAVING drill — add conditions one-by-one
            having_conditions = _split_and_conditions(having_text)
            having_drill: list[dict] = []
            prev_count    = no_having_count
            having_so_far = ""

            for i, hcond in enumerate(having_conditions):
                having_so_far += ("HAVING " if i == 0 else " AND ") + hcond
                test_sql = _inject_having(base_no_having, having_so_far)
                cur      = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"inner_having_add_cond_{i}")
                entry: dict = {
                    "condition":   hcond.strip(),
                    "rows_before": prev_count,
                    "rows_after":  cur,
                    "kills_rows":  prev_count > 0 and cur == 0,
                }
                if entry["kills_rows"]:
                    entry["likely_cause"]          = _likely_cause(hcond)
                    entry["threshold_suggestions"] = suggest_thresholds(
                        conn, hcond, base_no_having, metadata, threshold_bindings, threshold_config,
                        full_cte_sql=sql, param_mapping=param_mapping
                    )
                having_drill.append(entry)
                prev_count = cur

            findings["having_drill"] = having_drill

            # Also show the date/calendar context so the user knows the window
            view_info = _probe_source_views(conn, sql, metadata)
            if view_info:
                findings["source_view_info"] = view_info

        else:
            # Even without HAVING, 0 rows → upstream data availability problem
            findings["inner_having_is_culprit"] = False
            findings["issue"]       = "no_source_data"
            findings["explanation"] = (
                "Even removing the inner HAVING clause still returns 0 rows. "
                "The source tables/views contain no data for the current date window. "
                "Check each UNION ALL branch below for which data source is missing."
            )
            branch_counts = _probe_union_all_branches(conn, base_sql, metadata, run_logger=run_logger, output_dir=output_dir)
            if branch_counts:
                findings["union_all_branch_counts"] = branch_counts
            view_info = _probe_source_views(conn, sql, metadata)
            if view_info:
                findings["source_view_info"] = view_info

    else:
        # No inner HAVING found at all
        findings["issue"]       = "no_source_data"
        findings["explanation"] = (
            "The inner subquery returns 0 rows and no HAVING clause was found. "
            "Check the source tables below — one or more may be empty or have no "
            "data for the current batch date."
        )
        branch_counts = _probe_union_all_branches(conn, base_sql, metadata, run_logger=run_logger, output_dir=output_dir)
        if branch_counts:
            findings["union_all_branch_counts"] = branch_counts
        view_info = _probe_source_views(conn, sql, metadata)
        if view_info:
            findings["source_view_info"] = view_info

    return findings


# ──────────────────────────────────────────────────────────────────────────────
# JOIN DIAGNOSIS  (data availability for JOIN failures)
# ──────────────────────────────────────────────────────────────────────────────

def diagnose_joins(conn, sql: str, metadata: dict = None, run_logger=None) -> dict | None:
    parsed = parse_sql_structure(sql)
    joins  = parsed["joins"]
    if not joins:
        return None

    select_line = parsed["from_with_joins"].split("\n")[0]
    base_sql    = select_line + "\n" + parsed["base_from"]
    prev_count  = execute_count(conn, base_sql, metadata, run_logger=run_logger, step=6, label="joins_base_table_no_joins")
    print(f"\n  BASE TABLE ROWS (no joins): {prev_count:,}")
    current_sql = base_sql

    for idx, join in enumerate(joins):
        current_sql  += f"\n{join}"
        cur_count     = execute_count(conn, current_sql, metadata, run_logger=run_logger, step=6, label=f"joins_add_join_{idx}")
        print(f"  JOIN[{idx}]: {prev_count:,} → {cur_count:,}")
        print(f"    {join[:120]}")

        if prev_count > 0 and cur_count == 0:
            # Try to extract the joined table name for data availability info
            joined_table = _extract_joined_table(join)
            data_avail   = _probe_table_data(conn, joined_table, metadata) if joined_table else {}
            return {
                "failure_type":      "JOIN",
                "failure_condition": join,
                "rows_before":       prev_count,
                "rows_after":        cur_count,
                "likely_cause": (
                    "JOIN eliminates all rows — no matching keys between tables"
                ),
                "data_availability": {
                    "joined_table": joined_table,
                    **data_avail
                }
            }
        prev_count = cur_count

    return None


def _extract_joined_table(join_clause: str) -> str | None:
    m = re.search(r'\bJOIN\s+(\w+(?:\.\w+)?)', join_clause, re.IGNORECASE)
    return m.group(1) if m else None


def _probe_table_data(conn, table_name: str, metadata: dict) -> dict:
    """Checks row count and any date column range for the joined table."""
    info = {}
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        info["total_rows"] = cursor.fetchone()[0]
    except Exception:
        info["total_rows"] = None
    finally:
        cursor.close()

    # Try to find a date column and check its range
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT column_name FROM user_tab_columns
            WHERE  table_name = UPPER(:1)
              AND  data_type   IN ('DATE', 'TIMESTAMP')
            FETCH FIRST 1 ROWS ONLY
        """, [table_name.split(".")[-1]])
        row = cursor.fetchone()
        if row:
            date_col = row[0]
            cursor.close()
            cursor = conn.cursor()
            cursor.execute(f"SELECT MIN({date_col}), MAX({date_col}) FROM {table_name}")
            mn, mx = cursor.fetchone()
            if mn is not None:
                info["date_column"]  = date_col
                info["date_min"]     = str(mn)[:10]
                info["date_max"]     = str(mx)[:10]
                info["date_message"] = (
                    f"Table '{table_name}' has {info.get('total_rows', '?'):,} rows. "
                    f"Date column '{date_col}' ranges {info['date_min']} → {info['date_max']}."
                )
    except Exception:
        pass
    finally:
        cursor.close()

    return info


# ──────────────────────────────────────────────────────────────────────────────
# WHERE / HAVING DIAGNOSIS
# ──────────────────────────────────────────────────────────────────────────────

def _extract_alias_map(from_clause: str) -> dict[str, str]:
    """Parses a FROM clause and returns {alias: table_name}."""
    alias_map: dict[str, str] = {}
    skip = {'ON', 'WHERE', 'AND', 'OR', 'SET', 'HAVING', 'GROUP', 'ORDER',
            'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'FULL', 'JOIN',
            'SELECT', 'UNION', 'FROM'}

    normalized = from_clause
    normalized = re.sub(r'\b(?:INNER|LEFT|RIGHT|OUTER|CROSS|FULL)\s+(?:OUTER\s+)?JOIN\b', ',', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'^\bFROM\b\s*', '', normalized.strip(), flags=re.IGNORECASE)

    for part in normalized.split(','):
        stripped = part.strip()
        if not stripped or stripped.startswith('('):
            continue
        tokens = stripped.split()
        if not tokens:
            continue
        if tokens[0].upper() in skip:
            continue
        table = tokens[0]
        alias = tokens[1] if len(tokens) >= 2 else table.split('.')[-1]
        if alias.upper() not in skip:
            alias_map[alias.lower()] = table
    return alias_map


def _resolve_aliases(condition: str, sql: str) -> str | None:
    """Replace table aliases in a condition with actual table names.

    e.g. 't.BENEF_ACCT_ID = a.ACCT_INTRL_ID' -> 'MI_TRXN.BENEF_ACCT_ID = STG_ACCOUNT.ACCT_INTRL_ID'
    Returns None if no alias map could be built.
    """
    alias_map: dict[str, str] = {}
    skip = {'ON', 'WHERE', 'AND', 'OR', 'SET', 'HAVING', 'GROUP', 'ORDER',
            'INNER', 'LEFT', 'RIGHT', 'OUTER', 'CROSS', 'FULL', 'JOIN',
            'UNION', 'SELECT', 'FROM', 'LATERAL', 'ALL', 'ANY'}

    for m in re.finditer(r'\b(?:FROM|JOIN)\b\s+', sql, re.IGNORECASE):
        start = m.end()
        after = sql[start:]
        end_m = re.search(
            r'\b(?:WHERE|GROUP|HAVING|ORDER|UNION)\b|'
            r'\b(?:INNER|LEFT|RIGHT|CROSS|FULL)\s+(?:OUTER\s+)?JOIN\b|'
            r'\bON\b|'
            r'\(',
            after, re.IGNORECASE
        )
        if end_m:
            segment = after[:end_m.start()]
        else:
            segment = after

        for part in segment.split(','):
            tokens = part.strip().split()
            if len(tokens) >= 2:
                table = tokens[0]
                alias = tokens[1]
                if alias.upper() not in skip and not table.startswith('(') and alias.lower() not in alias_map:
                    alias_map[alias.lower()] = table

    if not alias_map:
        return None

    def _replace_alias(match):
        alias = match.group(1).lower()
        col = match.group(2)
        table = alias_map.get(alias, alias)
        return f"{table}.{col}"

    resolved = re.sub(r'\b(\w+)\.(\w+)', _replace_alias, condition, flags=re.IGNORECASE)
    return resolved


def _probe_column_values(conn, table: str, column: str, limit: int = 10) -> list[dict] | None:
    """
    For a non-numeric killer condition (equality/IN on a flag/text column),
    samples the actual distinct values present in that column so the AI
    recommendation can say what the data really contains instead of just
    "0 matching rows". Returns [{"value": ..., "count": ...}, ...] ordered by
    frequency, or None on any failure (never raises).
    """
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT {column}, COUNT(*) AS cnt
            FROM {table}
            WHERE {column} IS NOT NULL
            GROUP BY {column}
            ORDER BY cnt DESC
            FETCH FIRST {limit} ROWS ONLY
        """)
        rows = cursor.fetchall()
        if not rows:
            return None
        return [{"value": r[0], "count": r[1]} for r in rows]
    except Exception as e:
        logger.debug("Could not probe column values for %s.%s: %s", table, column, e)
        return None
    finally:
        cursor.close()


def _verify_killer_source(conn, killer_cond: str, base_from: str, metadata: dict) -> dict | None:
    """
    For a confirmed killer WHERE condition, looks up the source table and runs a
    targeted count to confirm data availability (or absence).

    Returns {"table", "filter_condition", "matching_rows"} or None on failure.

    Steps:
      1. Extract alias prefix from condition  (e.g. 'c.' from 'c.JRSDCN_CD IN ...')
      2. Map alias → table name via FROM clause
      3. Strip always-false guard ('N' = 'Y' OR ...) to get the real filter
      4. Remove alias prefix so filter can run directly against the table
      5. COUNT(*) with that filter to get matching_rows
    """
    try:
        alias_map = _extract_alias_map(base_from)

        # Find first alias.column reference in the condition
        prefix_m = re.search(r'\b(\w+)\.(\w+)', killer_cond)
        if not prefix_m:
            return None
        alias = prefix_m.group(1).lower()
        table = alias_map.get(alias)
        if not table:
            return None

        # Strip guard  ('N' = 'Y' OR <real_filter>)
        clean = killer_cond.strip()
        guard_m = re.match(r"^\(\s*'N'\s*=\s*'Y'\s+OR\s+(.+)\)\s*$", clean, re.IGNORECASE)
        if guard_m:
            clean = guard_m.group(1).strip()

        # Remove alias prefix from all references in the filter
        filter_cond = re.sub(r'\b' + re.escape(alias) + r'\.', '', clean, flags=re.IGNORECASE)

        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {filter_cond}")
            count = cursor.fetchone()[0]
        except Exception:
            return None
        finally:
            cursor.close()

        result = {"table": table, "filter_condition": filter_cond, "matching_rows": count}

        # For numeric threshold conditions, also fetch the actual column range
        # Handle both direct columns (v.Amount >= 100) and function-wrapped (DECODE(...) >= 100)
        col_m = re.search(r'\b(\w+)\s*[<>]=?\s*[\d.]+', filter_cond)
        if not col_m:
            # Try matching function-wrapped columns like DECODE('B','F',COL) >= 1000000
            col_m = re.search(
                r'(?:DECODE|COALESCE|NVL|GREATEST|LEAST)\s*\([^)]*?(\w+)\s*\)\s*[<>]=?\s*[\d.]+',
                filter_cond, re.IGNORECASE
            )
        if col_m and count == 0:
            col_name = col_m.group(1)
            cursor = conn.cursor()
            try:
                cursor.execute(f"SELECT MIN({col_name}), MAX({col_name}) FROM {table}")
                mn, mx = cursor.fetchone()
                if mx is not None:
                    result["col_min"] = mn
                    result["col_max"] = mx
                    result["col_name"] = col_name
            except Exception:
                pass
            finally:
                cursor.close()
        elif not col_m:
            # Not a numeric comparison — try equality/IN on a flag/text column
            # (e.g. INTRL_ORIG_ACCT_FL <> 'Y') and sample what values the
            # column actually holds. Deliberately NOT gated on count == 0:
            # this filter run in isolation can be nonzero (the value exists
            # somewhere in the table) even though the CTE's *combined*
            # conditions still yield 0 rows — the observed distribution is
            # useful context either way, so the AI isn't just told "filtering
            # it out" with nothing concrete to point at.
            eq_term = _extract_equality_terms(filter_cond)
            if eq_term:
                observed = _probe_column_values(conn, table, eq_term["col"])
                if observed:
                    result["col_name"] = eq_term["col"]
                    result["expected_value"] = eq_term["value"]
                    result["observed_values"] = observed
                    # Pre-compute which observed values satisfy the condition
                    # (see _value_satisfies_condition) so the AI never has to
                    # work out operator polarity itself.
                    result["satisfying_values"] = [
                        v for v in observed
                        if _value_satisfies_condition(eq_term["op"], eq_term["value"], v["value"])
                    ]

        return result
    except Exception:
        return None


def diagnose_where_conditions(
    conn, sql: str, metadata: dict = None,
    threshold_bindings: dict = None, threshold_config: dict = None,
    param_mapping: dict = None, run_logger=None, output_dir: str = ""
) -> dict | None:
    """
    Finds which WHERE condition(s) eliminate all rows using a "remove one" approach.

    Strategy: for each condition, run the query with that condition removed.
    If removing condition X restores rows, X is a killer.

    Advantages over "add one":
    - Never runs the Cartesian-product base query (keeps N-1 conditions)
    - Finds the TRUE killer even when an earlier condition also reduces rows
    - Works consistently for comma-join, explicit JOIN, and subquery CTEs
    """
    parsed     = parse_sql_structure(sql)
    base_sql   = parsed.get("from_with_joins_and_group") or parsed["from_with_joins"]
    conditions = parsed["where_conditions"]
    if not base_sql:
        logger.warning("parse_sql_structure returned empty from_with_joins — cannot diagnose WHERE")
        return None
    if not conditions:
        return None

    where_analysis: list[dict] = []
    primary_killer: str | None = None

    for i, cond in enumerate(conditions):
        remaining = [c for j, c in enumerate(conditions) if j != i]
        if remaining:
            test_sql = base_sql + "\nWHERE " + "\n  AND ".join(remaining)
        else:
            test_sql = base_sql
        cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"where_remove_cond_{i}")
        print(f"  WHERE remove[{i}]: rows_without = {cur:,}  | {cond.strip()[:100]}")
        entry = {
            "condition":              cond.strip(),
            "rows_without_this_cond": cur,
            "kills_rows":             cur > 0,
        }
        where_analysis.append(entry)
        if cur > 0 and primary_killer is None:
            primary_killer = cond.strip()

    if primary_killer is None and len(conditions) >= 2:
        # No single killer — try removing pairs
        for i in range(len(conditions)):
            for j in range(i + 1, len(conditions)):
                remaining = [c for k, c in enumerate(conditions) if k != i and k != j]
                test_sql = base_sql + ("\nWHERE " + "\n  AND ".join(remaining) if remaining else "")
                cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"where_remove_pair_{i}_{j}")
                if cur > 0:
                    primary_killer = "\n  AND ".join([conditions[i].strip(), conditions[j].strip()])
                    if run_logger:
                        run_logger.log(6, f"  WHERE remove[{i},{j}] pair: {cur:,} rows restored")
                    break
            if primary_killer is not None:
                break

    if primary_killer is None:
        # All conditions individually appear necessary — combination too restrictive
        return None

    # VERIFICATION: Comment out the killer in the resolved SQL and re-execute
    resolved_sql_file = ""
    if output_dir:
        extracted_dir = os.path.join(output_dir, "extracted_queries")
        if os.path.isdir(extracted_dir):
            dataset_files = [
                os.path.join(extracted_dir, f)
                for f in os.listdir(extracted_dir)
                if "resolved_function_dataset" in f.lower() and f.lower().endswith(".sql")
            ]
            if dataset_files:
                resolved_sql_file = max(dataset_files, key=os.path.getmtime)

    if resolved_sql_file:
        if run_logger:
            run_logger.log(6, f"VERIFICATION: Loading resolved SQL from {os.path.basename(resolved_sql_file)}")
        verification = verify_killer_condition(
            conn, primary_killer, resolved_sql_file, metadata,
            run_logger=run_logger, output_dir=output_dir
        )
        if run_logger:
            run_logger.log(6, f"VERIFICATION RESULT: {verification['message']}")

    # Build the SQL without the primary killer for OR drill-down / threshold lookup
    other_conditions = [c for c in conditions if c.strip() != primary_killer]
    base_without_killer = (
        base_sql + "\nWHERE " + "\n  AND ".join(other_conditions)
        if other_conditions else base_sql
    )

    # Verify the killer against its source table to get an exact "data insufficient" answer
    source_check = _verify_killer_source(
        conn, primary_killer, parsed.get("base_from", ""), metadata
    )

    # Build the human-readable conclusion from actual execution results
    likely = _likely_cause(primary_killer)
    if source_check is not None:
        tbl = source_check["table"]
        flt = source_check["filter_condition"]
        cnt = source_check["matching_rows"]
        if cnt == 0:
            if "col_max" in source_check:
                col  = source_check["col_name"]
                mx   = source_check["col_max"]
                likely = (
                    f"Thresholds are set too high — 0 records in '{tbl}' satisfy '{flt}'. "
                    f"Actual max value of {col} in {tbl} is {mx}. Consider lowering the threshold."
                )
            else:
                likely = (
                    f"Data insufficient in '{tbl}' — 0 records match '{flt}'. "
                    f"No data available for this filter on the current batch date."
                )
        else:
            likely = (
                f"{likely} "
                f"('{tbl}' has {cnt:,} records matching '{flt}', but the combination "
                f"with other join conditions eliminates all rows.)"
            )
    else:
        # For cross-table equalities in WHERE (e.g. t.col = a.col), run a targeted
        # join-data check so the explanation names the table(s) instead of saying
        # "Equality filter has no matching values in the data".
        try:
            join_explanation = _explain_where_equality(
                conn, primary_killer, parsed.get("base_from", ""), metadata
            )
            if join_explanation:
                likely = join_explanation
        except Exception:
            pass

    result = {
        "failure_type":      "WHERE",
        "failure_condition": primary_killer,
        "rows_before":       None,
        "rows_after":        0,
        "likely_cause":      likely,
        "where_analysis":    where_analysis,
        "threshold_suggestions": [],
    }
    if source_check is not None:
        result["source_check"] = source_check

    # Add verification result
    if resolved_sql_file:
        result["verification"] = verification
        if verification.get("verified"):
            result["likely_cause"] = (
                f"VERIFIED: Commenting out '{primary_killer[:80]}...' generates {verification['rows']:,} alerts. "
                f"This condition is the root cause."
            )
        else:
            result["likely_cause"] = (
                f"Condition '{primary_killer[:80]}...' identified as killer, "
                f"but commenting it out still yields 0 rows. Combination of conditions may be the issue."
            )

    # Try OR drill-down on the primary killer
    or_analysis = _drill_or_block(
        conn, primary_killer, base_without_killer,
        metadata, threshold_bindings or {}, threshold_config or {},
        full_cte_sql=sql, param_mapping=param_mapping, run_logger=run_logger
    )
    if or_analysis:
        result["or_analysis"]           = or_analysis
        result["threshold_suggestions"] = or_analysis.get("threshold_suggestions", [])
        result["likely_cause"] = (
            f"The WHERE condition is an OR block with {or_analysis['or_branch_count']} branches. "
            f"Branches {or_analysis['failing_branches']} all return 0 rows."
        )
    else:
        result["threshold_suggestions"] = suggest_thresholds(
            conn, primary_killer, base_without_killer,
            metadata, threshold_bindings or {}, threshold_config or {},
            full_cte_sql=sql, param_mapping=param_mapping
        )

    return result


def diagnose_having_conditions(
    conn, sql: str, metadata: dict = None,
    threshold_bindings: dict = None, threshold_config: dict = None,
    param_mapping: dict = None, run_logger=None, output_dir: str = ""
) -> dict | None:
    parsed     = parse_sql_structure(sql)
    base_sql   = parsed["before_having"]
    conditions = parsed["having_conditions"]
    if not base_sql or not conditions:
        return None

    prev_count = execute_count(conn, base_sql, metadata, run_logger=run_logger, step=6, label="having_before_any_conditions")
    print(f"\n  ROWS BEFORE HAVING filters: {prev_count:,}")
    having_sql = ""

    for idx, condition in enumerate(conditions):
        having_sql   += ("\nHAVING " if idx == 0 else "\n  AND ") + condition
        cur_count     = execute_count(conn, base_sql + having_sql, metadata, run_logger=run_logger, step=6, label=f"having_add_cond_{idx}")
        print(f"  HAVING[{idx}]: {prev_count:,} → {cur_count:,}")
        print(f"    {condition[:120]}")

        if prev_count > 0 and cur_count == 0:
            # VERIFICATION: Comment out the killer in the resolved SQL and re-execute
            resolved_sql_file = ""
            if output_dir:
                extracted_dir = os.path.join(output_dir, "extracted_queries")
                if os.path.isdir(extracted_dir):
                    dataset_files = [
                        os.path.join(extracted_dir, f)
                        for f in os.listdir(extracted_dir)
                        if "resolved_function_dataset" in f.lower() and f.lower().endswith(".sql")
                    ]
                    if dataset_files:
                        resolved_sql_file = max(dataset_files, key=os.path.getmtime)

            verification = None
            if resolved_sql_file:
                if run_logger:
                    run_logger.log(6, f"VERIFICATION: Loading resolved SQL from {os.path.basename(resolved_sql_file)}")
                verification = verify_killer_condition(
                    conn, condition.strip(), resolved_sql_file, metadata,
                    run_logger=run_logger, output_dir=output_dir
                )
                if run_logger:
                    run_logger.log(6, f"VERIFICATION RESULT: {verification['message']}")

            result = {
                "failure_type":      "HAVING",
                "failure_condition": condition,
                "rows_before":       prev_count,
                "rows_after":        cur_count,
                "likely_cause":      _likely_cause(condition),
                "threshold_suggestions": suggest_thresholds(
                    conn, condition, base_sql, metadata, threshold_bindings or {}, threshold_config or {},
                    full_cte_sql=sql, param_mapping=param_mapping
                ),
            }
            if verification:
                result["verification"] = verification
                if verification.get("verified"):
                    result["likely_cause"] = (
                        f"VERIFIED: Commenting out '{condition.strip()[:80]}...' generates {verification['rows']:,} alerts. "
                        f"This HAVING condition is the root cause."
                    )
                else:
                    result["likely_cause"] = (
                        f"⚠️ KILLER HAVING CONDITION FOUND (not verified): '{condition.strip()[:80]}...' "
                        f"Removing this condition restores rows. "
                        f"Verification failed: {verification.get('message', 'Could not locate in resolved SQL')}"
                    )
            return result

        prev_count = cur_count

    return None


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4: OUTER QUERY ANALYSIS (wrapping UNION or inner subquery)
# ──────────────────────────────────────────────────────────────────────────────

def diagnose_outer_union_query(
    conn, sql: str, metadata: dict, run_logger=None
) -> dict | None:
    """
    STEP 4: Analyze the outer query that wraps a UNION ALL block.
    Extracts the outer WHERE conditions and tests them individually.

    Returns a result dict or None if no outer conditions found.
    """
    step = 4
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()

    if run_logger:
        run_logger.section("STEP 4: Outer Query Analysis — checking conditions wrapping UNION")
        run_logger.log(step, "Loading: Outer Query of UNION — verifying wrapper conditions")
        run_logger.log_sql(step, "outer_union_query", sql)

    # Find outer WHERE (top-level, depth 0)
    where_pos = _find_top_level_clause("WHERE", clean)
    if where_pos == -1:
        if run_logger:
            run_logger.log(step, "No outer WHERE clause found — UNION block itself is the issue")
        return None

    # Find the subquery boundary: locate FROM (
    from_paren = re.search(r'FROM\s*\(', clean, re.IGNORECASE)
    if not from_paren:
        if run_logger:
            run_logger.log(step, "Cannot parse outer query structure — no FROM ( found")
        return None

    # Extract outer SELECT prefix and WHERE block
    outer_select = clean[:from_paren.start()].strip()
    after_from = clean[from_paren.end():]

    # Find matching closing paren for the subquery
    depth = 1
    subquery_end = -1
    for i, ch in enumerate(after_from):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                subquery_end = i
                break

    if subquery_end == -1:
        if run_logger:
            run_logger.log(step, "Cannot find subquery boundary")
        return None

    subquery_sql = after_from[:subquery_end].strip()
    alias_part = after_from[subquery_end:].strip()  # e.g. ") ot WHERE ..."

    # Extract outer WHERE conditions
    outer_where_match = re.search(r'\bWHERE\b(.+)$', alias_part, re.IGNORECASE | re.DOTALL)
    if not outer_where_match:
        if run_logger:
            run_logger.log(step, "No outer WHERE after subquery alias")
        return None

    outer_where_text = outer_where_match.group(1).strip()
    outer_conditions = _split_and_conditions(outer_where_text)

    if not outer_conditions:
        if run_logger:
            run_logger.log(step, "Outer WHERE has no parseable conditions")
        return None

    if run_logger:
        run_logger.log(step, f"Found {len(outer_conditions)} outer WHERE condition(s)")

    # Test: does subquery alone (without outer WHERE) return rows?
    subquery_count = execute_count(conn, subquery_sql, metadata, run_logger=run_logger, step=step, label="union_subquery_without_outer_where")
    if run_logger:
        run_logger.log(step, f"Subquery (without outer WHERE): {subquery_count:,} rows")

    if subquery_count == 0:
        if run_logger:
            run_logger.log(step, "Subquery itself returns 0 rows — outer WHERE is not the issue")
        return {
            "failure_type": "outer_union_subquery_empty",
            "explanation": f"The UNION subquery returns 0 rows even without the outer WHERE. The problem is inside the UNION branches, not the outer query.",
            "subquery_row_count": subquery_count,
            "outer_conditions": outer_conditions,
        }

    # Test each outer condition by removing one at a time
    result = {
        "failure_type": "outer_union_where",
        "subquery_row_count": subquery_count,
        "outer_conditions": [],
        "killing_condition": None,
    }

    full_outer_sql = f"{outer_select}\nFROM (\n{subquery_sql}\n) ot\nWHERE {outer_where_text}"
    full_outer_count = execute_count(conn, full_outer_sql, metadata, run_logger=run_logger, step=step, label="full_outer_union_query")
    if run_logger:
        run_logger.log(step, f"Full outer query (with all WHERE conditions): {full_outer_count:,} rows")

    if full_outer_count > 0:
        if run_logger:
            run_logger.log(step, "Outer query returns data — no outer WHERE issue")
        return None

    for i, cond in enumerate(outer_conditions):
        remaining = [c for j, c in enumerate(outer_conditions) if j != i]
        if remaining:
            test_sql = f"{outer_select}\nFROM (\n{subquery_sql}\n) ot\nWHERE {' AND '.join(remaining)}"
        else:
            test_sql = f"{outer_select}\nFROM (\n{subquery_sql}\n) ot"

        cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=step, label=f"outer_query_without_cond_{i+1}")
        entry = {
            "condition": cond.strip(),
            "rows_without_this_cond": cur,
            "kills_rows": cur > 0,
        }
        result["outer_conditions"].append(entry)

        if run_logger:
            status = "KILLS ROWS" if cur > 0 else "still 0"
            run_logger.log(step, f"  Remove condition {i+1}: {cur:,} rows ({status}) | {cond.strip()[:100]}")

        if cur > 0 and result["killing_condition"] is None:
            result["killing_condition"] = cond.strip()

    if result["killing_condition"]:
        result["explanation"] = f"Outer WHERE condition eliminates all rows: {result['killing_condition'][:200]}"
    else:
        result["explanation"] = "All outer WHERE conditions are individually necessary; the combination is too restrictive."

    return result


# ──────────────────────────────────────────────────────────────────────────────
# STEP 5: PROGRESSIVE OUTER QUERY DRILLING (strip layers until data found)
# ──────────────────────────────────────────────────────────────────────────────────────────────

def drill_outer_queries_recursive(
    conn, sql: str, metadata: dict, run_logger=None, max_depth=5
) -> dict | None:
    """
    STEP 5: Strip one outer SELECT/WHERE layer at a time and check if data appears.
    Continues until innermost query is reached or data is found.

    This handles queries like:
        SELECT * FROM (
          SELECT * FROM (
            SELECT ... UNION ALL ...
          ) t1 WHERE cond1
        ) t2 WHERE cond2
    """
    step_counter = [5]
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()

    if run_logger:
        run_logger.section("STEP 5: Progressive Outer Query Drilling — stripping layers until data found")

    layers = []
    current_sql = clean
    depth = 0

    while depth < max_depth:
        step = step_counter[0]
        layer_step = depth + 1

        # Try to find outer SELECT FROM ( ... ) WHERE pattern
        from_paren = re.search(r'FROM\s*\(', current_sql, re.IGNORECASE)
        if not from_paren:
            if run_logger:
                run_logger.log(step, f"Layer {layer_step}: No more outer SELECT FROM ( found — reached innermost query")
            break

        outer_select = current_sql[:from_paren.start()].strip()
        after_from = current_sql[from_paren.end():]

        # Find matching closing paren
        d = 1
        subquery_end = -1
        for i, ch in enumerate(after_from):
            if ch == '(':
                d += 1
            elif ch == ')':
                d -= 1
                if d == 0:
                    subquery_end = i
                    break

        if subquery_end == -1:
            break

        subquery_sql = after_from[:subquery_end].strip()
        alias_part = after_from[subquery_end:].strip()

        # Extract outer WHERE if any
        outer_where_match = re.search(r'\bWHERE\b(.+)$', alias_part, re.IGNORECASE | re.DOTALL)
        outer_where_text = outer_where_match.group(1).strip() if outer_where_match else None

        layer_info = {
            "layer": layer_step,
            "outer_select": outer_select,
            "has_outer_where": outer_where_text is not None,
            "outer_where": outer_where_text,
        }

        # Execute current layer
        layer_count = execute_count(conn, current_sql, metadata, run_logger=run_logger, step=step, label=f"outer_layer_{layer_step}")

        if run_logger:
            run_logger.log(step, f"Loading: Outer Query Analysis (Layer {layer_step})")
            run_logger.log(step, f"Layer {layer_step} result: {layer_count:,} rows")

            if outer_where_text:
                run_logger.log(step, f"Layer {layer_step} has outer WHERE: {outer_where_text[:150]}")
            else:
                run_logger.log(step, f"Layer {layer_step} has no outer WHERE — alias only: {alias_part[:50]}")

        if layer_count > 0:
            if run_logger:
                run_logger.log(step, f"Layer {layer_step} returned {layer_count:,} rows — data found at this level!")
                run_logger.summary(step, f"Data Found at Layer {layer_step}", [
                    f"This layer has data. The issue is in an outer layer's WHERE condition.",
                    f"Strip the outer layer's WHERE to restore rows.",
                ])
            return {
                "data_found_at_layer": layer_step,
                "row_count": layer_count,
                "layers": layers,
                "current_layer": layer_info,
                "explanation": f"Data exists at layer {layer_step} ({layer_count:,} rows). Outer layers are filtering it out."
            }

        layers.append(layer_info)

        # Strip this outer layer and go deeper
        if outer_where_text:
            next_sql = f"{outer_select}\nFROM (\n{subquery_sql}\n) {alias_part.split('WHERE')[0].strip()}"
        else:
            # Just strip the outer SELECT FROM ( ... ) alias wrapper
            next_sql = subquery_sql

        if next_sql == current_sql:
            if run_logger:
                run_logger.log(step, f"Layer {layer_step}: No further stripping possible")
            break

        current_sql = next_sql
        depth += 1
        step_counter[0] += 1

    # Reached innermost, still 0 rows
    if run_logger:
        run_logger.log(step_counter[0], f"All {len(layers)} outer layers stripped — innermost query still returns 0 rows")
        run_logger.summary(step_counter[0], "Innermost Query Still Empty", [
            f"Stripped {len(layers)} outer SELECT layers.",
            "The innermost query itself returns 0 rows.",
            "The issue is in the core UNION branches or inner subquery logic.",
        ])

    return {
        "data_found_at_layer": None,
        "row_count": 0,
        "layers_stripped": len(layers),
        "layers": layers,
        "innermost_sql": current_sql,
        "explanation": f"Even the innermost query returns 0 rows after stripping {len(layers)} outer layers. The issue is in the core query logic."
    }


# ──────────────────────────────────────────────────────────────────────────────
# FINAL QUERY: INNER QUERY DECOMPOSITION & DIAGNOSIS
# ──────────────────────────────────────────────────────────────────────────────

def _find_condition_line_in_sql(condition: str, sql_text: str) -> int | None:
    """
    Find the 1-based line number where a condition text appears in the original SQL.
    Handles conditions that span multiple lines. Returns None if not found.
    """
    if not condition or not sql_text:
        return None

    lines = sql_text.splitlines()
    header_offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped == '' or stripped.startswith('--'):
            header_offset += 1
        else:
            break
    if header_offset > 0:
        sql_text = '\n'.join(lines[header_offset:])

    def _normalize(text):
        """Collapse whitespace and normalize spaces around operators."""
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\s*([<>=!]+)\s*', r' \1 ', text)
        text = text.upper()
        text = re.sub(r'\bNOT\s+(.*?)\s+IS\s+NULL\b', r'\1 IS NOT NULL', text)
        return text

    def _try_match(search_text: str) -> int | None:
        search = _normalize(search_text)
        for lineno, line in enumerate(sql_text.splitlines(), start=1):
            line_norm = _normalize(line)
            if search in line_norm:
                return lineno

        search_short = search[:200]
        lines = sql_text.splitlines()
        for window in range(2, 5):
            for i in range(len(lines) - window + 1):
                block = ' '.join(lines[i:i + window])
                block_norm = _normalize(block)
                if search_short in block_norm:
                    return i + 1

        cond_words = [w for w in re.split(r'[\s()\'"=<>!,]+', search_text) if len(w) > 3 and w.upper() not in ('NULL', 'TRUE', 'FALSE', 'LIKE', 'BETWEEN')]
        if len(cond_words) >= 2:
            search_phrase = ' '.join(w.upper() for w in cond_words[:3])
            for lineno, line in enumerate(sql_text.splitlines(), start=1):
                if search_phrase in _normalize(line):
                    return lineno
            for window in range(2, 5):
                for i in range(len(lines) - window + 1):
                    block = ' '.join(lines[i:i + window])
                    if search_phrase in _normalize(block):
                        return i + 1
        return None

    result = _try_match(condition)
    if result is not None:
        return result + header_offset

    parts = _split_and_conditions(condition)
    if len(parts) > 1:
        for part in parts:
            result = _try_match(part.strip())
            if result is not None:
                return result + header_offset

    return None


def _find_subquery_regions(sql: str) -> list[dict]:
    """Find all parenthesised subquery regions, ordered by depth (deepest first)."""
    regions = []
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    i = 0
    paren_stack = []
    while i < len(clean):
        if clean[i] == '(':
            paren_stack.append(i)
        elif clean[i] == ')' and paren_stack:
            open_pos = paren_stack.pop()
            inner = clean[open_pos + 1:i].strip()
            if re.match(r'(?:SELECT|WITH)\b', inner, re.IGNORECASE):
                depth = clean[:open_pos].count('(') - clean[:open_pos].count(')')
                before = clean[:open_pos].rstrip().upper()
                if before.endswith('FROM') or re.search(r'\bFROM\s*$', before):
                    context = "FROM"
                elif re.search(r'\bIN\s*$', before):
                    context = "IN"
                elif before.endswith('EXISTS') or re.search(r'\bEXISTS\s*$', before):
                    context = "EXISTS"
                elif re.search(r'\bSELECT\s*$', before):
                    context = "SELECT clause"
                elif 'UNION' in before:
                    context = "UNION branch"
                else:
                    context = "subquery"
                regions.append({
                    "depth": depth,
                    "context": context,
                    "sql": inner,
                    "start": open_pos,
                    "end": i,
                })
        i += 1
    regions.sort(key=lambda r: r["depth"], reverse=True)
    return regions


def decompose_final_query(sql: str) -> list[dict]:
    """Decomposes final_query into inner queries at every nesting level.
    Returns list ordered innermost-first with: id, depth, context, sql, view_name, file_name."""
    clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
    all_queries = []
    subquery_regions = _find_subquery_regions(clean)
    for idx, region in enumerate(subquery_regions):
        all_queries.append({
            "id": idx + 1,
            "depth": region["depth"],
            "context": region["context"],
            "sql": region["sql"],
            "view_name": f"fq_inner_{idx + 1}",
            "file_name": f"{idx + 1:03d}_level{region['depth']}_{region['context'].lower().replace(' ', '_')}.sql",
        })
    full_id = len(all_queries) + 1
    all_queries.append({
        "id": full_id,
        "depth": 0,
        "context": "full_final_query",
        "sql": clean,
        "view_name": f"fq_inner_{full_id}",
        "file_name": f"{full_id:03d}_level0_full_final_query.sql",
    })
    return all_queries


def save_inner_query_files(inner_queries: list[dict], output_dir: str, run_logger=None) -> str:
    """Saves each inner query to parsed_ctes/final_query_inner/."""
    inner_dir = os.path.join(output_dir, "parsed_ctes", "final_query_inner")
    os.makedirs(inner_dir, exist_ok=True)
    for iq in inner_queries:
        file_path = os.path.join(inner_dir, iq["file_name"])
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"-- Inner Query: {iq['view_name']}\n")
            f.write(f"-- Depth: {iq['depth']}, Context: {iq['context']}\n\n")
            f.write(iq["sql"])
        if run_logger:
            run_logger.log(6, f"Saved inner query: {iq['view_name']} -> {file_path}")
    return inner_dir


def _create_inner_view(conn, name: str, sql: str) -> bool:
    cursor = conn.cursor()
    try:
        sql_clean = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL).strip()
        cursor.execute(f"CREATE OR REPLACE VIEW {name} AS\n{sql_clean}")
        return True
    except Exception as e:
        logger.warning("Failed to create view %s: %s", name, e)
        return False
    finally:
        cursor.close()


def _validate_inner_view(conn, name: str) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {name}")
        return cursor.fetchone()[0]
    except Exception as e:
        logger.warning("Failed to validate view %s: %s", name, e)
        return -1
    finally:
        cursor.close()


def eliminate_conditions_and_retry(
    conn, sql: str, metadata: dict, threshold_bindings: dict,
    threshold_config: dict, param_mapping: dict, run_logger=None,
    dataset_query_sql: str = None,
    dataset_query_raw: str = None
) -> dict:
    """Progressively eliminates WHERE and HAVING conditions to find the killer."""
    result = {
        "failure_type": None, "failure_condition": None,
        "rows_before_elimination": 0, "rows_after_elimination": 0,
        "elimination_phase": None, "having_analysis": [],
        "where_analysis": [], "likely_cause": None, "threshold_suggestions": [],
        "condition_line_number": None,
    }
    parsed = parse_sql_structure(sql)

    # Phase 1: HAVING elimination
    if parsed["having_conditions"]:
        if run_logger:
            run_logger.log(6, "--- Phase 1: Eliminating HAVING conditions ---")
        else:
            print("\n  --- Phase 1: Eliminating HAVING conditions ---")
        no_having_sql = parsed["before_having"]
        no_having_count = execute_count(conn, no_having_sql, metadata, run_logger=run_logger, step=6, label="eliminate_all_having")
        result["rows_after_elimination"] = no_having_count
        if no_having_count > 0:
            result["failure_type"] = "HAVING"
            result["elimination_phase"] = "HAVING"
            result["likely_cause"] = f"Removing all HAVING conditions restores {no_having_count:,} rows."
            for i, hcond in enumerate(parsed["having_conditions"]):
                remaining = [c for j, c in enumerate(parsed["having_conditions"]) if j != i]
                test_sql = parsed["before_having"] + ("\nHAVING " + "\n  AND ".join(remaining) if remaining else "")
                cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"having_remove_cond_{i}")
                entry = {"condition": hcond.strip(), "rows_without_this_cond": cur, "kills_rows": cur > 0}
                result["having_analysis"].append(entry)
                if run_logger:
                    status = "KILLS ROWS" if cur > 0 else "still 0"
                    run_logger.log(6, f"  Remove HAVING[{i}]: {cur:,} rows ({status}) | {hcond.strip()[:100]}")
                else:
                    print(f"  Remove HAVING[{i}]: {cur:,} rows ({status}) | {hcond.strip()[:100]}")
                if cur > 0 and result["failure_condition"] is None:
                    result["failure_condition"] = hcond.strip()
                    result["threshold_suggestions"] = suggest_thresholds(
                        conn, hcond, parsed["before_having"], metadata,
                        threshold_bindings, threshold_config, param_mapping=param_mapping
                    )
                    line_num = _find_condition_line_in_sql(hcond.strip(), dataset_query_raw)
                    result["condition_line_number"] = line_num
                    if run_logger:
                        if line_num:
                            run_logger.log(6, f"  *** Killer condition found at dataset query line {line_num} ***")
                        else:
                            run_logger.log(6, "  *** Killer condition — line number not found in dataset query ***")
                    else:
                        if line_num:
                            print(f"  *** Killer condition found at dataset query line {line_num} ***")
                        else:
                            print(f"  *** Killer condition — line number not found in dataset query ***")
            if result["failure_condition"] is None:
                result["failure_condition"] = "HAVING (combination of conditions)"
            return result

    # Phase 2: WHERE elimination
    if parsed["where_conditions"]:
        if run_logger:
            run_logger.log(6, "--- Phase 2: Eliminating WHERE conditions ---")
        else:
            print("\n  --- Phase 2: Eliminating WHERE conditions ---")
        base_sql = parsed.get("from_with_joins_and_group") or parsed["from_with_joins"]
        for i, wcond in enumerate(parsed["where_conditions"]):
            remaining = [c for j, c in enumerate(parsed["where_conditions"]) if j != i]
            test_sql = base_sql + ("\nWHERE " + "\n  AND ".join(remaining) if remaining else "")
            cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"where_remove_cond_{i}")
            entry = {"condition": wcond.strip(), "rows_without_this_cond": cur, "kills_rows": cur > 0}
            result["where_analysis"].append(entry)
            if run_logger:
                status = "KILLS ROWS" if cur > 0 else "still 0"
                run_logger.log(6, f"  Remove WHERE[{i}]: {cur:,} rows ({status}) | {wcond.strip()[:100]}")
            else:
                print(f"  Remove WHERE[{i}]: {cur:,} rows ({status}) | {wcond.strip()[:100]}")
            if cur > 0 and result["failure_type"] is None:
                result["failure_type"] = "WHERE"
                result["elimination_phase"] = "WHERE"
                result["rows_after_elimination"] = cur
                result["failure_condition"] = wcond.strip()
                result["likely_cause"] = _likely_cause(wcond)
                result["threshold_suggestions"] = suggest_thresholds(
                    conn, wcond, base_sql, metadata,
                    threshold_bindings, threshold_config, param_mapping=param_mapping
                )
                result["condition_line_number"] = _find_condition_line_in_sql(wcond.strip(), dataset_query_raw)
                # Same source-table probe diagnose_where_conditions() runs —
                # this deep-dive path (used for e.g. final_query/inner-query
                # CTEs) found a killer independently and never called it.
                source_check = _verify_killer_source(conn, wcond.strip(), base_sql, metadata)
                if source_check:
                    result["source_check"] = source_check
        if result["failure_type"] and result["failure_condition"]:
            return result

        # No single killer — try removing pairs
        wc = parsed["where_conditions"]
        PAIRWISE_MAX_CONDITIONS = 40
        if len(wc) >= 2 and len(wc) <= PAIRWISE_MAX_CONDITIONS:
            for i in range(len(wc)):
                for j in range(i + 1, len(wc)):
                    remaining = [c for k, c in enumerate(wc) if k != i and k != j]
                    test_sql = base_sql + ("\nWHERE " + "\n  AND ".join(remaining) if remaining else "")
                    cur = execute_count(conn, test_sql, metadata, run_logger=run_logger, step=6, label=f"where_remove_pair_{i}_{j}")
                    if cur > 0:
                        result["failure_type"] = "WHERE"
                        result["elimination_phase"] = "WHERE"
                        result["rows_after_elimination"] = cur
                        combined = "\n  AND ".join([wc[i].strip(), wc[j].strip()])
                        result["failure_condition"] = combined
                        result["likely_cause"] = _likely_cause(combined)
                        result["condition_line_number"] = _find_condition_line_in_sql(combined, dataset_query_raw)
                        if run_logger:
                            run_logger.log(6, f"  Remove WHERE[{i},{j}] pair: {cur:,} rows restored")
                        return result
        elif len(wc) > PAIRWISE_MAX_CONDITIONS:
            if run_logger:
                run_logger.log(6, f"--- Skipping pairwise search: {len(wc)} conditions exceeds limit of {PAIRWISE_MAX_CONDITIONS} ---")
            else:
                print(f"\n  --- Skipping pairwise search: {len(wc)} conditions exceeds limit of {PAIRWISE_MAX_CONDITIONS} ---")

    # Phase 3: Remove ALL conditions
    if run_logger:
        run_logger.log(6, "--- Phase 3: Removing ALL conditions ---")
    else:
        print("\n  --- Phase 3: Removing ALL conditions ---")
    bare_count = 0

    # For derived-table queries (SELECT ... FROM (subquery) alias WHERE ...),
    # extract the inner subquery and count it directly instead of using the
    # broken regex that only captures up to the first FROM keyword.
    if _has_top_level_derived_table(sql):
        inner_sql = _extract_derived_table_inner_sql(sql)
        if inner_sql:
            bare_count = execute_count(conn, inner_sql, metadata, run_logger=run_logger, step=6, label="bare_select_from_derived")
            result["rows_after_elimination"] = bare_count
            if bare_count > 0:
                result["failure_type"] = "WHERE_OR_HAVING_COMBINATION"
                result["elimination_phase"] = "ALL"
                result["failure_condition"] = "Combination of WHERE and HAVING conditions on derived table"
                result["likely_cause"] = f"Inner subquery returns {bare_count:,} rows but outer WHERE conditions eliminate all of them."
                return result

    # Fallback for non-derived-table queries: strip WHERE/HAVING with regex
    select_match = re.match(r'(SELECT\b.*?\bFROM\b\s+.*?)$', sql, re.IGNORECASE | re.DOTALL)
    if select_match:
        bare_count = execute_count(conn, select_match.group(1).strip(), metadata, run_logger=run_logger, step=6, label="bare_select_from")
        result["rows_after_elimination"] = bare_count
        if bare_count > 0:
            result["failure_type"] = "WHERE_OR_HAVING_COMBINATION"
            result["elimination_phase"] = "ALL"
            result["failure_condition"] = "Combination of WHERE and HAVING conditions"
            result["likely_cause"] = f"Without any conditions, query returns {bare_count:,} rows."
            return result

    # Phase 4: JOIN elimination — try each JOIN to find the killer
    # Skip for derived-table queries (outer FROM wraps a subquery) because
    # the JOIN logic is inside the inner subquery, not at the outer level.
    # Stripping JOINs from a "FROM (...)" produces invalid SQL.
    if parsed["joins"] and not _has_top_level_derived_table(sql):
        if run_logger:
            run_logger.log(6, "--- Phase 4: Eliminating JOIN conditions ---")
        else:
            print("\n  --- Phase 4: Eliminating JOIN conditions ---")
        select_line = parsed["from_with_joins"].split("\n")[0]
        base_sql = select_line + "\n" + parsed["base_from"]
        prev_count = execute_count(conn, base_sql, metadata, run_logger=run_logger, step=6, label="joins_ecr_base")
        current_sql = base_sql
        for idx, join in enumerate(parsed["joins"]):
            current_sql += f"\n{join}"
            cur_count = execute_count(conn, current_sql, metadata, run_logger=run_logger, step=6, label=f"joins_ecr_add_{idx}")
            if prev_count > 0 and cur_count == 0:
                result["failure_type"] = "WHERE_CONDITION"
                result["elimination_phase"] = "JOIN"
                result["rows_before_elimination"] = prev_count
                result["rows_after_elimination"] = cur_count
                result["failure_condition"] = join
                result["likely_cause"] = "JOIN/WHERE condition returns 0 rows — no matching keys across the source tables."
                result["condition_line_number"] = _find_condition_line_in_sql(join, dataset_query_raw)
                return result
            prev_count = cur_count

    # Phase 5: Probe UNION ALL branches
    branch_counts = _probe_union_all_branches(conn, sql, metadata, run_logger=run_logger)
    if branch_counts:
        empty_branches = [b for b in branch_counts if b["row_count"] == 0]
        if empty_branches:
            result["failure_type"] = "NO_SOURCE_DATA"
            result["elimination_phase"] = "ALL"
            result["data_availability"] = {
                "union_all_branch_counts": branch_counts,
                "rows_without_outer_where": bare_count,
            }
            result["likely_cause"] = "No data in any UNION ALL branch — source tables or views may be empty for the current batch date."
            return result

    result["failure_type"] = "unknown"
    result["elimination_phase"] = "ALL"
    result["likely_cause"] = "All conditions eliminated but still 0 rows — issue may be in JOINs or subquery logic."
    return result


def diagnose_inner_query_sequence(
    conn, inner_queries: list[dict], metadata: dict,
    threshold_bindings: dict, threshold_config: dict,
    param_mapping: dict, run_logger=None,
    dataset_query_sql: str = None,
    dataset_query_raw: str = None
) -> list[dict]:
    """Executes each inner query one by one from innermost to outermost."""
    results = []
    created_views = []
    last_healthy = None  # Tracks {"sql": str, "count": int, "view_name": str} of last healthy inner query
    for iq in inner_queries:
        view_name = iq["view_name"]
        sql = iq["sql"]
        if run_logger:
            run_logger.section(f"DIAGNOSING INNER QUERY: {view_name} (depth={iq['depth']}, context={iq['context']})")
            run_logger.log(6, f"Executing inner query: {view_name}")
            run_logger.log_sql(6, view_name, sql)
        else:
            print(f"\n{'=' * 80}")
            print(f"  INNER QUERY: {view_name} (depth={iq['depth']}, context={iq['context']})")
            print(f"{'=' * 80}")
        if not _create_inner_view(conn, view_name, sql):
            # Try direct execution — correlated subqueries (outer refs cause ORA-00904)
            try:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM (\n{sql}\n) t")
                direct_count = cursor.fetchone()[0]
                cursor.close()
            except Exception as e:
                err = str(e).upper()
                if run_logger:
                    run_logger.log(6, f"  ⚠️ {view_name} is a correlated subquery — skipping (references outer scope: {e})")
                else:
                    print(f"  ⚠️ {view_name} is a correlated subquery — skipping (references outer scope)")
                continue
            if direct_count > 0:
                if run_logger:
                    run_logger.log(6, f"  ✅ {view_name} is healthy — {direct_count:,} rows")
                else:
                    print(f"  ✅ {view_name} is healthy — {direct_count:,} rows")
                continue
            # View failed, direct count returns 0 — try condition elimination
            diag = eliminate_conditions_and_retry(
                conn, sql, metadata, threshold_bindings, threshold_config,
                param_mapping, run_logger=run_logger,
                dataset_query_sql=dataset_query_sql,
                dataset_query_raw=dataset_query_raw
            )
            result = {
                "cte_name": f"final_query/{view_name}",
                "failure_type": diag["failure_type"],
                "failure_condition": diag["failure_condition"],
                "rows_before": 0, "rows_after": diag["rows_after_elimination"],
                "likely_cause": diag["likely_cause"],
                "threshold_suggestions": diag.get("threshold_suggestions", []),
                "data_availability": diag.get("data_availability", {}),
                "inner_query": iq,
                "elimination_phase": diag["elimination_phase"],
                "having_analysis": diag.get("having_analysis", []),
                "where_analysis": diag.get("where_analysis", []),
                "condition_line_number": diag.get("condition_line_number"),
                "created_views": list(created_views),
            }
            results.append(result)
            if diag["failure_type"] and diag["failure_condition"]:
                if run_logger:
                    run_logger.log(6, f"  Root cause found in {view_name}: {diag['failure_type']} — {diag['failure_condition'][:100]}")
                else:
                    print(f"  Root cause found in {view_name}: {diag['failure_type']} — {diag['failure_condition'][:100]}")
                break
            continue
        created_views.append(view_name)
        count = _validate_inner_view(conn, view_name)
        if count < 0:
            # View was created but querying it failed — likely a correlated subquery
            # that references outer scope columns. Drop the view and try direct execution.
            try:
                cursor = conn.cursor()
                cursor.execute(f"DROP VIEW {view_name}")
                cursor.close()
            except Exception:
                pass
            try:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM (\n{sql}\n) t")
                direct_count = cursor.fetchone()[0]
                cursor.close()
            except Exception as e:
                if run_logger:
                    run_logger.log(6, f"  ⚠️ {view_name} is a correlated subquery — skipping (references outer scope: {e})")
                else:
                    print(f"  ⚠️ {view_name} is a correlated subquery — skipping (references outer scope)")
                continue
            if direct_count > 0:
                if run_logger:
                    run_logger.log(6, f"  ✅ {view_name} is healthy — {direct_count:,} rows")
                else:
                    print(f"  ✅ {view_name} is healthy — {direct_count:,} rows")
                last_healthy = {"sql": sql, "count": direct_count, "view_name": view_name, "iq": iq}
                continue
            # Direct count returned 0 — proceed to condition elimination
            count = 0
        if count > 0:
            if run_logger:
                run_logger.log(6, f"  ✅ {view_name} is healthy — {count:,} rows")
            else:
                print(f"  ✅ {view_name} is healthy — {count:,} rows")
            last_healthy = {"sql": sql, "count": count, "view_name": view_name, "iq": iq}
            for prev in results:
                if prev.get("inner_query") and prev["inner_query"]["id"] < iq["id"]:
                    prev["data_found_at_outer_level"] = iq["id"]
                    prev["data_found_at_view"] = view_name
            continue
        if run_logger:
            run_logger.log(6, f"  ⚠️ {view_name} returns 0 rows — starting condition elimination...")
        else:
            print(f"  ⚠️ {view_name} returns 0 rows — starting condition elimination...")

        # Pre-check: if this query wraps a derived table and the last healthy
        # inner query had data, sample it and check for threshold kills.
        if _has_top_level_derived_table(sql) and last_healthy:
            prev_iq = last_healthy["iq"]
            prev_sql = last_healthy["sql"]
            prev_count = last_healthy["count"]
            if prev_sql and prev_count > 0:
                if run_logger:
                    run_logger.log(6, f"  Sampling data from {prev_iq['view_name']} ({prev_iq.get('context', '')}) before elimination...")
                sample_data = _sample_inner_query_data(conn, prev_sql, metadata, run_logger=run_logger, known_row_count=prev_count)
                threshold_info = _check_threshold_kill(sample_data, sql, run_logger=run_logger)
                if threshold_info:
                    killers_summary = ", ".join([
                        f"{k['column']} {k['operator']} {k['threshold']} (actual max={k['actual_max']})"
                        for k in threshold_info["killers"]
                    ])
                    # Best-effort match each killer column against the scenario's
                    # configured KDD_TSHLD entries (names rarely match the raw SQL
                    # column exactly, so this is inclusion-based, not exact) — when
                    # it hits, the AI recommendation gets the threshold's real
                    # configured value and plain-English description, not just the
                    # SQL literal.
                    matched_config = {}
                    for k in threshold_info["killers"]:
                        col_upper = (k.get("column") or "").upper()
                        for tshld_name, entry in (threshold_config or {}).items():
                            name_upper = (tshld_name or "").upper()
                            if name_upper and (name_upper in col_upper or col_upper in name_upper):
                                matched_config[tshld_name] = entry

                    # Concrete, deterministic suggested threshold values —
                    # never the raw actual_max/actual_min (see
                    # compute_threshold_kill_suggestion's docstring for why).
                    threshold_suggestions = []
                    for k in threshold_info["killers"]:
                        cfg = _match_threshold_config_entry(k, matched_config)
                        calc = compute_threshold_kill_suggestion(k, cfg)
                        threshold_suggestions.append({
                            "column": k.get("column"),
                            "suggestion": _format_threshold_kill_suggestion_text(k, calc),
                        })

                    diag = {
                        "failure_type": "THRESHOLD_KILL",
                        "failure_condition": killers_summary,
                        "rows_after_elimination": 0,
                        "elimination_phase": "OUTER_WHERE",
                        "likely_cause": (
                            f"Inner query {prev_iq['view_name']} returns {prev_count:,} rows "
                            f"but outer WHERE thresholds eliminate all. {killers_summary}."
                        ),
                        "threshold_suggestions": threshold_suggestions,
                        "data_availability": {
                            "inner_query_name": prev_iq["view_name"],
                            "inner_query_rows": prev_count,
                            "sample_rows": threshold_info["sample_rows"],
                            "column_stats": threshold_info["column_stats"],
                            "threshold_killers": threshold_info["killers"],
                            "threshold_config": matched_config,
                        },
                        "having_analysis": [],
                        "where_analysis": [],
                        "condition_line_number": None,
                    }
                    result = {
                        "cte_name": f"final_query/{view_name}",
                        "failure_type": diag["failure_type"],
                        "failure_condition": diag["failure_condition"],
                        "rows_before": 0, "rows_after": diag["rows_after_elimination"],
                        "likely_cause": diag["likely_cause"],
                        "threshold_suggestions": diag.get("threshold_suggestions", []),
                        "data_availability": diag.get("data_availability", {}),
                        "inner_query": iq,
                        "elimination_phase": diag["elimination_phase"],
                        "having_analysis": diag.get("having_analysis", []),
                        "where_analysis": diag.get("where_analysis", []),
                        "condition_line_number": diag.get("condition_line_number"),
                        "created_views": list(created_views),
                    }
                    results.append(result)
                    if run_logger:
                        run_logger.log(6, f"  Threshold kill detected: {killers_summary}")
                    else:
                        print(f"  Threshold kill detected: {killers_summary}")
                    break

        diag = eliminate_conditions_and_retry(
            conn, sql, metadata, threshold_bindings, threshold_config,
            param_mapping, run_logger=run_logger,
            dataset_query_sql=dataset_query_sql,
            dataset_query_raw=dataset_query_raw
        )
        result = {
            "cte_name": f"final_query/{view_name}",
            "failure_type": diag["failure_type"],
            "failure_condition": diag["failure_condition"],
            "rows_before": 0, "rows_after": diag["rows_after_elimination"],
            "likely_cause": diag["likely_cause"],
            "threshold_suggestions": diag.get("threshold_suggestions", []),
            "data_availability": diag.get("data_availability", {}),
            "inner_query": iq,
            "elimination_phase": diag["elimination_phase"],
            "having_analysis": diag.get("having_analysis", []),
            "where_analysis": diag.get("where_analysis", []),
            "condition_line_number": diag.get("condition_line_number"),
            "created_views": list(created_views),
        }
        results.append(result)
        if diag["failure_type"] and diag["failure_condition"]:
            if run_logger:
                run_logger.log(6, f"  Root cause found in {view_name}: {diag['failure_type']} — {diag['failure_condition'][:100]}")
            else:
                print(f"  Root cause found in {view_name}: {diag['failure_type']} — {diag['failure_condition'][:100]}")
            break
    return results


# ──────────────────────────────────────────────────────────────────────────────
# CTE TOPOLOGICAL SORT (dependency-ordered diagnosis)
# ──────────────────────────────────────────────────────────────────────────────

def _topological_sort_ctes(ctes: list[dict], cte_dependencies: dict, run_logger=None) -> list[dict]:
    """
    Sort CTEs so that dependencies are diagnosed before dependents.
    Uses Kahn's algorithm. Only considers edges where BOTH CTEs are in the
    input list (the empty CTEs to diagnose). CTEs whose dependencies are
    healthy (not in the empty set) are treated as having in_degree=0.

    Falls back to original order on circular dependencies.
    """
    if not ctes:
        return ctes

    empty_names_lower = {cte["name"].lower() for cte in ctes}
    # Map lowercase name → cte dict for quick lookup
    cte_by_lower = {cte["name"].lower(): cte for cte in ctes}

    # Build in-degree map and adjacency list
    in_degree = {}   # lowercase cte name → count of empty dependencies
    graph = {}       # lowercase dep name → [dependent lowercase names]

    for cte in ctes:
        name_lower = cte["name"].lower()
        deps = cte_dependencies.get(cte["name"], []) or cte_dependencies.get(name_lower, [])
        # Only count deps that are ALSO in the empty set
        empty_deps = [d.lower() for d in deps if d.lower() in empty_names_lower]
        in_degree[name_lower] = len(empty_deps)
        for d in empty_deps:
            graph.setdefault(d, []).append(name_lower)

    # Start with CTEs that have no empty dependencies (independent CTEs)
    queue = [name for name, deg in in_degree.items() if deg == 0]
    sorted_lower = []

    while queue:
        # Pop first (preserve original order for ties)
        current = queue.pop(0)
        sorted_lower.append(current)
        for dependent in graph.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Any CTEs not sorted → circular dependency → append in original order
    remaining = [cte["name"].lower() for cte in ctes if cte["name"].lower() not in set(sorted_lower)]
    sorted_lower.extend(remaining)

    # Convert back to cte dicts in sorted order
    sorted_ctes = [cte_by_lower[name] for name in sorted_lower if name in cte_by_lower]

    if run_logger and len(ctes) > 1:
        original_order = [cte["name"] for cte in ctes]
        sorted_order = [cte["name"] for cte in sorted_ctes]
        if original_order != sorted_order:
            run_logger.log(6, f"CTE diagnosis order (topological): {sorted_order}")
        else:
            run_logger.log(6, f"CTE diagnosis order (unchanged): {sorted_order}")

    return sorted_ctes


# ──────────────────────────────────────────────────────────────────────────────
# MAIN DIAGNOSTIC RUNNER
# ──────────────────────────────────────────────────────────────────────────────

def run_granular_cte_diagnostics(
    conn,
    ctes:     list[dict],
    metadata: dict = None,
    run_logger=None,
    dataset_query_sql: str = None,
    dataset_query_raw: str = None,
    dataset_query_file: str = None,
) -> list[dict]:
    """
    Runs progressive diagnostics on a list of CTEs.

    For each failing CTE:
      0. (final_query only) Decomposes into inner queries, executes innermost-first,
         eliminates WHERE/HAVING conditions on any failing inner query.
      1. Checks dependencies exist
      2. Full COUNT — skip if healthy
      3. Diagnoses JOINs  → data availability
      4. Diagnoses WHERE  → threshold suggestions + OR drill-down
      5. Diagnoses HAVING → threshold suggestions
      6. UNKNOWN fallback → inner subquery analysis
    """
    tshld_set_id = (metadata or {}).get("tshld_set_id")
    threshold_bindings = _get_threshold_bindings(conn, tshld_set_id)
    threshold_config   = _get_threshold_config(conn, tshld_set_id)
    if threshold_bindings:
        msg = f"Loaded {len(threshold_bindings)} threshold bindings from KDD_TSHLD_BINDING"
        print(f"\n  {msg}")
        if run_logger:
            run_logger.log(6, msg)
    if threshold_config:
        msg = f"Loaded {len(threshold_config)} threshold definitions from KDD_TSHLD"
        print(f"  {msg}")
        if run_logger:
            run_logger.log(6, msg)

    # Build col→param mapping from the main SQL so literal-value conditions in the
    # dataset SQL can be resolved to their KDD_TSHLD threshold names.
    output_dir = (metadata or {}).get("output_dir", "")
    param_mapping = _extract_main_sql_param_mapping(output_dir) if output_dir else {}

    if param_mapping and threshold_config:
        print("\n  THRESHOLD PARAMETERS (from main SQL @params → KDD_TSHLD):")
        if run_logger:
            run_logger.log(6, "THRESHOLD PARAMETERS (from main SQL @params → KDD_TSHLD):")
        seen: set = set()
        for col_lower, ops in sorted(param_mapping.items()):
            for op, param_name in sorted(ops.items()):
                if param_name in seen:
                    continue
                seen.add(param_name)
                entry = threshold_config.get(param_name)
                if entry:
                    line = f"  {param_name}: curr={entry.get('curr')}, min={entry.get('min')}, max={entry.get('max')}"
                    print(f"    {param_name}: curr={entry.get('curr')}, min={entry.get('min')}, max={entry.get('max')}")
                    if run_logger:
                        run_logger.log(6, line)

    results = []

    # Load CTE dependencies for dependency-aware diagnosis.
    # When a CTE depends on another CTE that already failed, we skip its deep
    # diagnosis and report "upstream_dependency" instead of "no_source_data".
    cte_dependencies: dict = {}
    if output_dir:
        dep_file = os.path.join(output_dir, "parsed_ctes", "dependencies.json")
        if os.path.exists(dep_file):
            try:
                with open(dep_file, "r", encoding="utf-8") as f:
                    cte_dependencies = json.load(f)
                if run_logger:
                    run_logger.log(6, f"Loaded CTE dependencies from {dep_file}")
            except Exception as e:
                logger.debug("Could not load dependencies.json: %s", e)

    failed_cte_names: set[str] = set()  # lowercase names of CTEs diagnosed as failed

    # ── Topological sort: diagnose dependencies before dependents ──
    # Independent CTEs all get full diagnosis.
    # Dependent CTEs are only diagnosed after their dependencies (and skipped
    # if the dependency already failed).
    ctes = _topological_sort_ctes(ctes, cte_dependencies, run_logger=run_logger)

    for cte in ctes:
        cte_name = cte["name"]
        sql      = cte["sql"]
        cte_name_lower = cte_name.lower()

        # Rebuild failed CTE set from results so far (robust against continue/break)
        failed_cte_names = {
            r["cte_name"].lower() for r in results
            if r.get("failure_type") and r.get("failure_type") not in (None, "healthy")
        }

        # ── Dependency-aware skip ──
        # If any CTE this one depends on already failed, skip deep diagnosis.
        deps = cte_dependencies.get(cte_name, [])
        failed_deps = [d for d in (deps or []) if d in failed_cte_names]
        if failed_deps:
            upstream = failed_deps[0]
            if run_logger:
                run_logger.section(f"DIAGNOSING CTE: {cte_name}")
                run_logger.log(6, f"Skipping deep diagnosis — upstream CTE '{upstream}' already failed")
            else:
                print("\n" + "=" * 100)
                print(f"  DIAGNOSING CTE: {cte_name}")
                print("=" * 100)
                print(f"  ⏭  Skipping — upstream CTE '{upstream}' already failed")
            result = {
                "cte_name":             cte_name,
                "failure_type":         "upstream_dependency",
                "failure_condition":    None,
                "rows_before":          None,
                "rows_after":           None,
                "likely_cause":         (
                    f"CTE '{cte_name}' is empty because upstream CTE '{upstream}' returned 0 rows. "
                    f"Fix '{upstream}' first — this CTE will be resolved automatically."
                ),
                "upstream_cte":         upstream,
                "failed_dependencies":  failed_deps,
                "threshold_suggestions": [],
                "data_availability":    {},
            }
            results.append(result)
            _print_root_cause(cte_name, result)
            continue

        if run_logger:
            run_logger.section(f"DIAGNOSING CTE: {cte_name}")
            run_logger.log(6, f"Loading: Diagnosing CTE '{cte_name}' — progressive condition analysis")
            run_logger.log_sql(6, f"CTE_{cte_name}_for_diagnosis", sql)
        else:
            print("\n" + "=" * 100)
            print(f"  DIAGNOSING CTE: {cte_name}")
            print("=" * 100)

        # Step 1: dependencies
        if run_logger:
            run_logger.log(6, f"Checking dependencies for {cte_name}...")
        else:
            print("\n  Checking dependencies...")
        missing = check_dependency_views_exist(conn, sql)
        if missing:
            result = {
                "cte_name":             cte_name,
                "failure_type":         "missing_dependencies",
                "failure_condition":    None,
                "rows_before":          None,
                "rows_after":           None,
                "likely_cause":         f"Missing tables or views: {', '.join(missing)}",
                "missing_views":        missing,
                "threshold_suggestions": [],
                "data_availability":    {},
            }
            results.append(result)
            _print_root_cause(cte_name, result)
            if run_logger:
                run_logger.log(6, f"Result: Missing dependencies — {', '.join(missing)}")
            continue

        # Step 2: full count
        try:
            full_count = execute_count(conn, sql, metadata, run_logger=run_logger, step=6, label=f"CTE_{cte_name}_full_count")
        except Exception as e:
            result = {
                "cte_name":             cte_name,
                "failure_type":         "execution_error",
                "failure_condition":    None,
                "rows_before":          None,
                "rows_after":           None,
                "likely_cause":         f"SQL execution error: {e}",
                "threshold_suggestions": [],
                "data_availability":    {},
            }
            results.append(result)
            _print_root_cause(cte_name, result)
            continue

        if full_count > 0:
            if run_logger:
                run_logger.log(6, f"Result: CTE '{cte_name}' is healthy — {full_count:,} rows")
            else:
                print(f"\n  ✅ CTE is healthy — {full_count:,} rows returned")
            continue

        if run_logger:
            run_logger.log(6, f"CTE '{cte_name}' returns 0 rows — starting diagnosis...")
        else:
            print("\n  ⚠️  CTE returns 0 rows — starting diagnosis...")

        result = {
            "cte_name":             cte_name,
            "failure_type":         None,
            "failure_condition":    None,
            "rows_before":          None,
            "rows_after":           None,
            "likely_cause":         None,
            "threshold_suggestions": [],
            "data_availability":    {},
        }

        # ── NEW: Inner query decomposition for final_query ──
        if cte_name == "final_query":
            if run_logger:
                run_logger.log(6, "--- Decomposing final_query into inner queries ---")
            else:
                print("\n  --- Decomposing final_query into inner queries ---")
            inner_queries = decompose_final_query(sql)
            if inner_queries and len(inner_queries) > 1:
                save_inner_query_files(inner_queries, output_dir, run_logger=run_logger)
                inner_results = diagnose_inner_query_sequence(
                    conn, inner_queries, metadata,
                    threshold_bindings, threshold_config,
                    param_mapping, run_logger=run_logger,
                    dataset_query_sql=dataset_query_sql,
                    dataset_query_raw=dataset_query_raw,
                )
                if inner_results:
                    # Find the result with the real diagnosis (not execution_error)
                    first = None
                    for r in inner_results:
                        ft = r.get("failure_type")
                        if ft and ft != "execution_error" and r.get("failure_condition"):
                            first = r
                            break
                    # Fallback: use first result if no real diagnosis found
                    if first is None:
                        first = inner_results[0]
                    result.update(first)
                    result["_inner_query_views"] = first.get("created_views", [])
                    results.append(result)
                    _print_root_cause(cte_name, result)
                    if run_logger:
                        run_logger.log(6, f"Result: {cte_name} — {result.get('failure_type', 'unknown')} — {result.get('likely_cause', '')[:200]}")
                        da = result.get("data_availability", {})
                        if da.get("rows_without_outer_where") is not None:
                            run_logger.log(6, f"  Rows without outer WHERE: {da['rows_without_outer_where']:,}")
                        if da.get("rows_without_inner_having") is not None:
                            run_logger.log(6, f"  Rows without inner HAVING: {da['rows_without_inner_having']:,}")
                        if da.get("union_all_branch_counts"):
                            for b in da["union_all_branch_counts"]:
                                status = "OK" if b["row_count"] > 0 else "EMPTY"
                                run_logger.log(6, f"  Branch {b['branch_num']:2d} {status} {b['row_count']:>8,} FROM {b['from_table']}")
                    continue

        # Step 3: JOINs
        try:
            if run_logger:
                run_logger.log(6, "--- Checking JOINs ---")
            else:
                print("\n  --- Checking JOINs ---")
            join_result = diagnose_joins(conn, sql, metadata, run_logger=run_logger)
            if join_result:
                result.update(join_result)
                results.append(result)
                _print_root_cause(cte_name, result)
                if run_logger:
                    run_logger.log(6, f"Result: JOIN issue found — {join_result.get('failure_condition', '')[:100]}")
                    if join_result.get("join_data_availability"):
                        for j in join_result["join_data_availability"]:
                            run_logger.log(6, f"  JOIN {j.get('join_condition', '')[:80]}: rows={j.get('rows', 0):,}")
                continue
            if run_logger:
                run_logger.log(6, "No JOIN issues found.")
            else:
                print("  No JOIN issues found.")
        except Exception as e:
            msg = f"JOIN diagnosis error: {e}"
            print(f"  ❌ {msg}")
            if run_logger:
                run_logger.log(6, f"ERROR: {msg}")

        # Step 4: WHERE
        try:
            if run_logger:
                run_logger.log(6, "--- Checking WHERE conditions ---")
            else:
                print("\n  --- Checking WHERE conditions ---")
            where_result = diagnose_where_conditions(
                conn, sql, metadata, threshold_bindings, threshold_config,
                param_mapping=param_mapping, run_logger=run_logger,
                output_dir=output_dir
            )
            if where_result:
                result.update(where_result)
                results.append(result)
                _print_root_cause(cte_name, result)
                if run_logger:
                    run_logger.log(6, f"Result: WHERE issue found — {where_result.get('failure_condition', '')[:100]}")
                    verification = where_result.get("verification")
                    if verification:
                        run_logger.log(6, f"  VERIFICATION: {verification.get('message', '')}")
                    if where_result.get("where_analysis"):
                        for w in where_result["where_analysis"]:
                            flag = "KILLS" if w.get("kills_rows") else "OK"
                            run_logger.log(6, f"  [{flag}] rows={w.get('rows_without_this_cond', 0):>8,} | {w.get('condition', '')[:80]}")
                    if where_result.get("threshold_suggestions"):
                        for s in where_result["threshold_suggestions"]:
                            run_logger.log(6, f"  Suggestion: {s.get('column')} — {s.get('suggestion', '')[:100]}")
                continue
            if run_logger:
                run_logger.log(6, "No WHERE issues found.")
            else:
                print("  No WHERE issues found.")
        except Exception as e:
            msg = f"WHERE diagnosis error: {e}"
            print(f"  ❌ {msg}")
            if run_logger:
                run_logger.log(6, f"ERROR: {msg}")

        # Step 5: HAVING
        try:
            if run_logger:
                run_logger.log(6, "--- Checking HAVING conditions ---")
            else:
                print("\n  --- Checking HAVING conditions ---")
            having_result = diagnose_having_conditions(
                conn, sql, metadata, threshold_bindings, threshold_config,
                param_mapping=param_mapping, run_logger=run_logger,
                output_dir=output_dir
            )
            if having_result:
                result.update(having_result)
                results.append(result)
                _print_root_cause(cte_name, result)
                if run_logger:
                    run_logger.log(6, f"Result: HAVING issue found — {having_result.get('failure_condition', '')[:100]}")
                    verification = having_result.get("verification")
                    if verification:
                        run_logger.log(6, f"  VERIFICATION: {verification.get('message', '')}")
                    if having_result.get("having_analysis"):
                        for h in having_result["having_analysis"]:
                            flag = "KILLS" if h.get("kills_rows") else "OK"
                            run_logger.log(6, f"  [{flag}] rows={h.get('rows_without_this_cond', 0):>8,} | {h.get('condition', '')[:80]}")
                continue
            if run_logger:
                run_logger.log(6, "No HAVING issues found.")
            else:
                print("  No HAVING issues found.")
        except Exception as e:
            msg = f"HAVING diagnosis error: {e}"
            print(f"  ❌ {msg}")
            if run_logger:
                run_logger.log(6, f"ERROR: {msg}")

        # Step 6: UNKNOWN — deep dive into inner subquery
        if run_logger:
            run_logger.log(6, "--- UNKNOWN: analysing inner subquery ---")
        else:
            print("\n  --- UNKNOWN: analysing inner subquery ---")
        try:
            inner_analysis = _analyze_inner_subquery(
                conn, sql, metadata, threshold_bindings, threshold_config,
                param_mapping=param_mapping, run_logger=run_logger,
                output_dir=output_dir, cte_name=cte_name,
                dataset_query_raw=dataset_query_raw
            )
            result["data_availability"] = inner_analysis

            killer_found = None
            for branch in inner_analysis.get("union_all_branch_counts", []):
                fa = branch.get("failure_analysis", {})
                if fa.get("killing_where_condition"):
                    killer_found = fa
                    break

            if killer_found:
                result["failure_type"] = "where_condition"
                result["failure_condition"] = killer_found.get("killing_where_condition")
                result["likely_cause"] = killer_found.get("explanation", "Killer condition found in UNION branch")
                if killer_found.get("verification"):
                    result["verification"] = killer_found["verification"]
                if killer_found.get("source_check"):
                    result["source_check"] = killer_found["source_check"]
            elif inner_analysis.get("issue") == "where_combination":
                result["failure_type"] = "where_combination"
                verification = inner_analysis.get("verification")
                rows_before = inner_analysis.get("rows_without_outer_where", 0)

                # Set WHERE condition line numbers from the findings
                where_cond_lines = inner_analysis.get("where_condition_lines", [])
                if where_cond_lines:
                    result["where_condition_lines"] = where_cond_lines
                    first_line = inner_analysis.get("first_condition_line")
                    if first_line:
                        result["condition_line_number"] = first_line
                    # Set the first condition as the failure_condition for display
                    first_cond = where_cond_lines[0].get("condition", "")
                    if first_cond:
                        result["failure_condition"] = first_cond

                if isinstance(verification, dict):
                    result["verification"] = verification
                    if verification.get("error"):
                        result["likely_cause"] = (
                            f"Inner query returns {rows_before:,} rows before outer WHERE, "
                            f"but verification SQL failed: {verification['error']}. "
                            f"The combined WHERE conditions are the likely root cause."
                        )
                    elif verification.get("verified"):
                        result["likely_cause"] = (
                            f"VERIFIED: Inner query returns {rows_before:,} rows before outer WHERE. "
                            f"Replacing WHERE clause with 1=1 generates {verification['rows']:,} alerts. "
                            f"The combined WHERE conditions are the root cause."
                        )
                    elif verification.get("conditions_commented", 0) > 0:
                        # Deeper investigation was done — use its real results
                        deeper = inner_analysis.get("deeper_investigation", {})
                        if deeper and deeper.get("failure_condition"):
                            result["likely_cause"] = deeper.get("explanation", "")
                            result["failure_condition"] = deeper["failure_condition"]
                            if deeper.get("condition_line_number"):
                                result["condition_line_number"] = deeper["condition_line_number"]
                        elif deeper:
                            result["likely_cause"] = deeper.get("explanation", "")
                        else:
                            result["likely_cause"] = (
                                f"Inner query returns {rows_before:,} rows before outer WHERE. "
                                f"Replaced WHERE clause with 1=1 but still 0 alerts — "
                                f"check the final query or other CTEs for additional filtering."
                            )
                    else:
                        result["likely_cause"] = (
                            f"Inner query returns {rows_before:,} rows before outer WHERE, but no single WHERE "
                            f"condition eliminates rows on its own. The combined conditions are too restrictive. "
                            f"Verification could not replace WHERE clause in resolved SQL — "
                            f"manually comment out WHERE conditions to confirm."
                        )
                else:
                    result["likely_cause"] = inner_analysis.get("explanation", "")
            elif inner_analysis.get("issue") in ("inner_having", "no_source_data", "parse_failed"):
                result["failure_type"] = inner_analysis["issue"]
                result["likely_cause"] = inner_analysis.get("explanation", "")
                result["data_availability"] = inner_analysis
            else:
                result["failure_type"] = "unknown"
                result["likely_cause"] = (
                    "All individual filters passed but CTE still returns 0 rows. "
                    "Likely a combination of conditions or a data availability issue."
                )
        except Exception as e:
            print(f"  ❌ Inner subquery analysis error: {e}")
            result["failure_type"] = "unknown"
            result["likely_cause"] = f"Inner subquery analysis error: {e}"
        results.append(result)
        _print_root_cause(cte_name, result)
        if run_logger:
            run_logger.section(f"ROOT CAUSE SUMMARY: {cte_name}")
            run_logger.log(6, f"CTE: {cte_name}")
            run_logger.log(6, f"Failure Type: {result.get('failure_type', 'unknown').upper()}")
            if result.get("failure_condition"):
                run_logger.log(6, f"Killer Condition: {result['failure_condition']}")
            if result.get("condition_line_number"):
                run_logger.log(6, f"Dataset Query Line: {result['condition_line_number']}")
            if result.get("likely_cause"):
                run_logger.log(6, f"Likely Cause: {result['likely_cause']}")
            rb = result.get("rows_before")
            ra = result.get("rows_after")
            if rb is not None:
                run_logger.log(6, f"Rows Before: {rb:,}")
                run_logger.log(6, f"Rows After: {ra:,}")
            da = result.get("data_availability", {})
            if da:
                if result.get("failure_type") == "THRESHOLD_KILL":
                    inner_name = da.get("inner_query_name", "N/A")
                    inner_rows = da.get("inner_query_rows", 0)
                    run_logger.log(6, f"Inner query ({inner_name}): {inner_rows:,} rows")
                    for killer in da.get("threshold_killers", []):
                        run_logger.log(6, f"  Killer: {killer['column']} {killer['operator']} {killer['threshold']} (actual max={killer.get('actual_max', '?')})")
                    stats = da.get("column_stats", {})
                    if stats:
                        for col, s in stats.items():
                            run_logger.log(6, f"  {col}: min={s.get('min','?')}, max={s.get('max','?')}, avg={s.get('avg','?')}")
                    sample = da.get("sample_rows", [])
                    if sample:
                        run_logger.log(6, f"Sample rows (first {len(sample)}):")
                        for i, row in enumerate(sample):
                            vals = ", ".join([f"{k}={v}" for k, v in list(row.items())[:8]])
                            run_logger.log(6, f"  [{i+1}] {vals}")
                elif da.get("explanation"):
                    run_logger.log(6, da["explanation"])
                if da.get("rows_without_outer_where") is not None:
                    run_logger.log(6, f"Rows without outer WHERE: {da['rows_without_outer_where']:,}")
                if da.get("rows_without_inner_having") is not None:
                    run_logger.log(6, f"Rows without inner HAVING: {da['rows_without_inner_having']:,}")
                verification = da.get("verification")
                if verification:
                    run_logger.log(6, f"VERIFICATION: {verification.get('message', '')}")
                if da.get("union_all_branch_counts"):
                    run_logger.log(6, "UNION ALL branch row counts:")
                    for b in da["union_all_branch_counts"]:
                        status = "OK" if b["row_count"] > 0 else "EMPTY"
                        run_logger.log(6, f"  Branch {b['branch_num']:2d} {status} {b['row_count']:>8,} FROM {b['from_table']}")
            ha = result.get("having_analysis", [])
            if ha:
                run_logger.log(6, "HAVING ANALYSIS:")
                for h in ha:
                    flag = "KILLS ROWS" if h.get("kills_rows") else "OK"
                    run_logger.log(6, f"  {flag} rows={h.get('rows_without_this_cond', 0):>8,} | {h.get('condition', '')[:80]}")
            wa = result.get("where_analysis", [])
            if wa:
                run_logger.log(6, "WHERE ANALYSIS:")
                for w in wa:
                    flag = "KILLS ROWS" if w.get("kills_rows") else "OK"
                    run_logger.log(6, f"  {flag} rows={w.get('rows_without_this_cond', 0):>8,} | {w.get('condition', '')[:80]}")
            sug = result.get("threshold_suggestions", [])
            if sug:
                run_logger.log(6, "THRESHOLD SUGGESTIONS:")
                for s in sug:
                    run_logger.log(6, f"  Column: {s.get('column')} — {s.get('suggestion', '')[:100]}")

    # Resolve table aliases in failure_condition for human-readable display
    # Also fill missing condition_line_number and add source_file
    import os as _os
    source_file = _os.path.basename(dataset_query_file) if dataset_query_file else (dataset_query_file or "")
    if not source_file and dataset_query_raw:
        source_file = "dataset_query.sql"

    cte_sql_map = {cte["name"].lower(): cte["sql"] for cte in ctes}
    for r in results:
        fc = r.get("failure_condition")
        if not fc:
            continue

        # Add source_file to every result with a failure_condition
        if source_file:
            r["source_file"] = source_file

        # Fill missing condition_line_number
        if not r.get("condition_line_number"):
            # Try dataset query first, then CTE's own SQL
            for search_sql in (dataset_query_raw, cte_sql_map.get(r["cte_name"].lower(), "")):
                if search_sql:
                    ln = _find_condition_line_in_sql(fc, search_sql)
                    if ln:
                        r["condition_line_number"] = ln
                        break

        # Try CTE-specific SQL first, fall back to first CTE or dataset query
        cte_sql = cte_sql_map.get(r["cte_name"].lower()) or next(iter(cte_sql_map.values()), None)
        if not cte_sql and dataset_query_sql:
            cte_sql = dataset_query_sql
        if cte_sql:
            resolved = _resolve_aliases(fc, cte_sql)
            if resolved and resolved != fc:
                r["resolved_condition"] = resolved

    return results


# ──────────────────────────────────────────────────────────────────────────────
# PRINTERS
# ──────────────────────────────────────────────────────────────────────────────

def _print_root_cause(cte_name: str, result: dict):
    print("\n" + "=" * 100)
    print("  ❌ ALERT NOT GENERATING — ROOT CAUSE FOUND")
    print("=" * 100)
    print(f"  CTE          : {cte_name}")
    print(f"  Failure type : {result.get('failure_type', 'unknown').upper()}")
    iq = result.get("inner_query")
    if iq:
        print(f"  Inner query  : {iq['view_name']} (depth={iq['depth']}, context={iq['context']})")
        print(f"  Inner file   : parsed_ctes/final_query_inner/{iq['file_name']}")
    elim = result.get("elimination_phase")
    if elim:
        print(f"  Elimination  : Phase {elim}")
    if result.get("failure_condition"):
        print(f"  Condition    : {result['failure_condition'][:300]}")
    rb = result.get("rows_before")
    ra = result.get("rows_after")
    if rb is not None:
        print(f"  Rows before  : {rb:,}")
        print(f"  Rows after   : {ra:,}")
    if result.get("likely_cause"):
        print(f"  Likely cause : {result['likely_cause']}")
    verification = result.get("verification")
    if verification:
        print(f"  Verification : {verification.get('message', '')}")
    ln = result.get("condition_line_number")
    if ln:
        print(f"  Dataset query line: {ln}")

    ha = result.get("having_analysis", [])
    if ha:
        print("\n  HAVING ANALYSIS:")
        for h in ha:
            flag = "❌ KILLS ROWS" if h.get("kills_rows") else "✅"
            print(f"    {flag}  rows={h['rows_without_this_cond']:>8,}  |  {h['condition'][:100]}")

    wa = result.get("where_analysis", [])
    if wa:
        print("\n  WHERE ANALYSIS:")
        for w in wa:
            flag = "❌ KILLS ROWS" if w.get("kills_rows") else "✅"
            print(f"    {flag}  rows={w['rows_without_this_cond']:>8,}  |  {w['condition'][:100]}")

    sug = result.get("threshold_suggestions", [])
    if sug:
        print("\n  THRESHOLD SUGGESTIONS:")
        for s in sug:
            print(f"\n  Column: {s.get('column')}")
            if s.get("param_min"):
                print(f"    KDD_TSHLD_BINDING param (min): {s['param_min']}")
            if s.get("param_max"):
                print(f"    KDD_TSHLD_BINDING param (max): {s['param_max']}")
            print(f"    {s.get('suggestion', '')}")

    da = result.get("data_availability", {})
    if da:
        # THRESHOLD_KILL: show column stats and killer details
        if result.get("failure_type") == "THRESHOLD_KILL":
            print("\n  THRESHOLD KILL DETAILS:")
            inner_name = da.get("inner_query_name", "N/A")
            inner_rows = da.get("inner_query_rows", 0)
            print(f"    Inner query ({inner_name}): {inner_rows:,} rows")
            for killer in da.get("threshold_killers", []):
                print(f"\n    Killer: {killer['column']} {killer['operator']} {killer['threshold']}")
                print(f"      Actual max: {killer.get('actual_max', 'N/A')}")
                print(f"      Actual min: {killer.get('actual_min', 'N/A')}")
            stats = da.get("column_stats", {})
            if stats:
                print("\n    Column statistics:")
                for col, s in stats.items():
                    print(f"      {col}: min={s.get('min','?')}, max={s.get('max','?')}, avg={s.get('avg','?')}")
            sample = da.get("sample_rows", [])
            if sample:
                print(f"\n    Sample rows (first {len(sample)}):")
                for i, row in enumerate(sample):
                    vals = ", ".join([f"{k}={v}" for k, v in list(row.items())[:8]])
                    print(f"      [{i+1}] {vals}")
            print("\n    💡 Consider lowering the threshold or checking if business date is correct.")
            return  # Skip generic data availability for threshold kills
        print("\n  DATA AVAILABILITY:")
        if da.get("explanation"):
            print(f"    {da['explanation']}")
        if da.get("rows_without_outer_where") is not None:
            print(f"    Rows without outer WHERE : {da['rows_without_outer_where']:,}")
        if da.get("rows_without_inner_having") is not None:
            print(f"    Rows without inner HAVING: {da['rows_without_inner_having']:,}")
        if da.get("having_drill"):
            print("  Inner HAVING analysis:")
            for h in da["having_drill"]:
                flag = "❌ KILLS ROWS" if h.get("kills_rows") else "✅"
                print(f"    {flag}  {h['rows_before']:,} → {h['rows_after']:,}  |  {h['condition'][:100]}")
                if h.get("likely_cause"):
                    print(f"       → {h['likely_cause']}")
        if da.get("union_all_branch_counts"):
            print("  UNION ALL branch row counts:")
            for b in da["union_all_branch_counts"]:
                status = "✅" if b["row_count"] > 0 else "❌"
                print(f"    Branch {b['branch_num']:2d}  {status}  {b['row_count']:>8,}  FROM {b['from_table']}")
        if da.get("source_view_info"):
            print("  Source view / table data:")
            for v in da["source_view_info"]:
                rc = v.get("row_count")
                rc_str = f"{rc:,}" if isinstance(rc, int) else str(rc)
                print(f"    {v['name']:<40}  {rc_str} rows")
                if v.get("clndr_data"):
                    for k, val in v["clndr_data"].items():
                        print(f"      {k}: {val}")
        if da.get("date_message"):
            print(f"    {da['date_message']}")

    print("=" * 100)


def display_granular_results(results: list[dict]):
    print("\n" + "=" * 100)
    print("  DIAGNOSTIC SUMMARY")
    print("=" * 100)
    if not results:
        print("\n  ✅ All CTEs returned rows — alert pipeline looks healthy.")
        print("     If alerts are still not generating, check the final query thresholds.")
        print("=" * 100)
        return
    for r in results:
        print(f"\n  CTE          : {r['cte_name']}")
        print(f"  Failure type : {r.get('failure_type', 'unknown').upper()}")
        if r.get("failure_condition"):
            print(f"  Condition    : {r['failure_condition'][:300]}")
        rb = r.get("rows_before")
        ra = r.get("rows_after")
        if rb is not None:
            print(f"  Rows before  : {rb:,}  →  Rows after: {ra:,}")
        if r.get("likely_cause"):
            print(f"  Likely cause : {r['likely_cause']}")
        for s in r.get("threshold_suggestions", []):
            print(f"\n  THRESHOLD — {s.get('column')}: {s.get('suggestion', '')}")
        da = r.get("data_availability", {})
        if da.get("explanation"):
            print(f"  DATA AVAILABILITY: {da['explanation']}")
        print("-" * 100)
    print("\n  Root cause: alerts not generating because the above CTE(s) return 0 rows.")
    print("=" * 100)