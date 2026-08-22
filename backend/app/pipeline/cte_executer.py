import os
import json
import logging

from db_connect import connect_to_oracle
from path_manager import get_latest_output_directory
from sql_diagnostics import run_granular_cte_diagnostics, display_granular_results, _probe_union_all_branches

# ----------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# LOAD PATHS
# ----------------------------------------------------------------------

def get_parsed_cte_directory():
    base = get_latest_output_directory()
    path = os.path.join(base, "parsed_ctes")

    if not os.path.exists(path):
        raise FileNotFoundError(f"Parsed CTE directory not found: {path}")

    return path


def load_dependencies(parsed_dir):
    file = os.path.join(parsed_dir, "dependencies.json")

    if not os.path.exists(file):
        return {}

    with open(file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ctes(parsed_dir):
    files = sorted([
        os.path.join(parsed_dir, f)
        for f in os.listdir(parsed_dir)
        if f.endswith(".sql") and f != "final_query.sql"
    ])

    ctes = []

    for f in files:
        with open(f, "r", encoding="utf-8") as file:
            sql = file.read()

        name = os.path.basename(f).split("_", 1)[1].replace(".sql", "")

        # Strip -- single-line comments only; keep SQL intact
        cleaned = "\n".join(
            line for line in sql.splitlines()
            if not line.strip().startswith("--")
        ).strip()

        ctes.append({
            "name": name,
            "sql":  cleaned
        })

    return ctes


def load_final_query(parsed_dir):
    path = os.path.join(parsed_dir, "final_query.sql")

    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_metadata(output_dir=None):
    base = output_dir if output_dir else get_latest_output_directory()
    path = os.path.join(base, "metadata.json")

    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# ORACLE HELPERS
# ----------------------------------------------------------------------

def create_view(conn, name: str, sql: str):
    """Creates or replaces an Oracle view for the given CTE SQL."""
    cursor = conn.cursor()
    try:
        logger.info("Creating view: %s", name)
        cursor.execute(f"CREATE OR REPLACE VIEW {name} AS\n{sql}")
        # DDL auto-commits in Oracle — explicit commit is harmless but not needed
    finally:
        cursor.close()


def validate_view(conn, name: str) -> int:
    """Returns the row count of the named view."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {name}")
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def execute_final_query(conn, sql: str, run_logger=None) -> int:
    """Runs the final alert query and prints up to 10 sample rows."""
    cursor = conn.cursor()
    try:
        if run_logger:
            run_logger.log_sql(3, "final_query", sql)

        cursor.execute(sql)
        rows = cursor.fetchmany(10)

        if run_logger:
            if rows:
                run_logger.summary(3, "Result: FINAL QUERY — ALERTS GENERATED", [
                    f"Returned {len(rows)} rows",
                ])
            else:
                run_logger.log(3, "Result: FINAL QUERY — 0 rows returned")
        else:
            print("\n" + "=" * 80)
            print("FINAL QUERY RESULT")
            print("=" * 80)

            if rows:
                print("\n✅ ALERTS GENERATED\n")
                for i, r in enumerate(rows, 1):
                    print(f"  {i}. {r}")
            else:
                print("\n⚠️  NO DATA RETURNED")

        return len(rows)

    finally:
        cursor.close()


def drop_views(conn, views: list[str]):
    """Drops all created views in reverse order (dependency-safe)."""
    cursor = conn.cursor()
    try:
        if not getattr(drop_views, '_logged', False):
            print("\nDropping views...")
        for v in reversed(views):
            try:
                cursor.execute(f"DROP VIEW {v}")
                if not getattr(drop_views, '_logged', False):
                    print(f"  Dropped: {v}")
            except Exception as e:
                logger.warning("Could not drop view %s: %s", v, e)
    finally:
        cursor.close()


# ----------------------------------------------------------------------
# DIAGNOSTICS WRAPPER
# ----------------------------------------------------------------------

def run_diagnostics(conn, cte: dict, metadata: dict, run_logger=None):
    """
    Runs granular diagnostics on a single failing CTE.
    Wraps the CTE dict in a list as required by run_granular_cte_diagnostics.
    All working CTEs are already live as Oracle views so the failing CTE
    can reference them during diagnosis.
    """
    try:
        results = run_granular_cte_diagnostics(conn, [cte], metadata, run_logger=run_logger)
        display_granular_results(results)
    except Exception as e:
        print(f"\n❌ Diagnostics failed unexpectedly: {e}")


# ----------------------------------------------------------------------
# UNION BRANCH ANALYSIS (Step 4)
# ----------------------------------------------------------------------

def analyze_union_branches(conn, sql: str, metadata: dict, run_logger=None) -> list[dict]:
    """
    STEP 4: Analyze UNION ALL branches of the final query.
    Returns branch results with row counts.
    """
    step = 4

    if run_logger:
        run_logger.section("STEP 4: UNION Branch Analysis — checking each branch of final query")
        run_logger.log(step, "Loading: UNION Branch Analysis")
        run_logger.log_sql(step, "final_query_for_union_analysis", sql)

    branches = _probe_union_all_branches(conn, sql, metadata)

    any_has_data = False
    branch_results = []

    for b in branches:
        status = "✅" if b["row_count"] > 0 else "❌"
        label = b.get("branch_label", f"Branch {b['branch_num']}")

        if run_logger:
            run_logger.log(step, f"EXECUTING SQL: UNION {label} — {b['row_count']:,} rows {status}")
            run_logger.log_sql(step, f"union_branch_{b['branch_num']}", b["preview"])

        if b["row_count"] > 0:
            any_has_data = True

        branch_results.append(b)

        if not run_logger:
            print(f"  Branch {b['branch_num']:2d}  {status}  {b['row_count']:>8,}  FROM {b['from_table']}")
            if b.get("failure_analysis"):
                fa = b["failure_analysis"]
                print(f"    Issue: {fa.get('explanation', 'unknown')}")

    if run_logger:
        if any_has_data:
            run_logger.log(step, "Result: At least one UNION branch has data — proceeding to outer query analysis")
        else:
            run_logger.log(step, "Result: ALL UNION branches return 0 rows — diagnosing empty branches")

    return branch_results, any_has_data


# ----------------------------------------------------------------------
# MAIN EXECUTOR
# ----------------------------------------------------------------------

def execute_ctes(
    conn,
    ctes: list[dict],
    final_query: str,
    metadata: dict,
    run_logger=None
) -> dict:
    """
    Execute ALL CTEs (does NOT stop at first empty), then run final query.
    Returns a dict with cte_results, empty_ctes, final_count.
    """
    step = 3

    if run_logger:
        run_logger.section("STEP 3: CTE Executer — checking ALL CTEs (will not stop at first failure)")
        run_logger.log(step, "Loading: CTE Executer — validating each CTE as Oracle view")

    created_views = []
    cte_results = []
    empty_ctes = []

    for idx, cte in enumerate(ctes, 1):
        name = cte["name"]
        sql = cte["sql"]

        if run_logger:
            run_logger.log(step, f"--- CTE {idx}/{len(ctes)}: {name} ---")
            run_logger.log_sql(step, f"CTE_{name}", sql)

        if not run_logger:
            print("\n" + "-" * 80)
            print(f"CTE: {name}")
            print("-" * 80)

        try:
            # Create view so dependent CTEs can reference it
            create_view(conn, name, sql)
            created_views.append(name)

            count = validate_view(conn, name)

            cte_results.append({
                "name": name,
                "rows": count,
                "status": "ok" if count > 0 else "empty"
            })

            if count == 0:
                empty_ctes.append(cte)
                if run_logger:
                    run_logger.log(step, f"Result: {name} — 0 rows ❌ — marking for diagnosis (continuing to check remaining CTEs)")
                else:
                    print(f"\n⚠️  EMPTY CTE: {name} (0 rows) — will diagnose after checking all CTEs")
            else:
                if run_logger:
                    run_logger.log(step, f"Result: {name} — {count:,} rows ✅")
                else:
                    print(f"\n✅ {name} returned {count:,} rows")

        except Exception as e:
            if run_logger:
                run_logger.log(step, f"Result: {name} — EXECUTION ERROR ❌ — {e}")
            else:
                print(f"\n❌ FAILED CTE: {name}")
                print(f"   {e}")
            cte_results.append({
                "name": name,
                "rows": 0,
                "status": "error",
                "error": str(e)
            })

    # Summary of all CTEs
    ok_count = sum(1 for r in cte_results if r["status"] == "ok")
    empty_count = len(empty_ctes)
    error_count = sum(1 for r in cte_results if r["status"] == "error")

    if run_logger:
        run_logger.summary(step, "CTE Execution Summary", [
            f"Total CTEs: {len(ctes)}",
            f"Passed (rows > 0): {ok_count}",
            f"Empty (rows = 0): {empty_count}",
            f"Errors: {error_count}",
        ])
        if empty_ctes:
            empty_names = [c["name"] for c in empty_ctes]
            run_logger.log(step, f"Empty CTEs found: {empty_names}")

    # Run final query
    final_count = 0
    if empty_count == 0:
        if run_logger:
            run_logger.log(step, "All CTEs have data — executing final query")
        final_count = execute_final_query(conn, final_query, run_logger=run_logger)
    else:
        if run_logger:
            run_logger.log(step, f"{empty_count} empty CTE found — skipping final query, running diagnostics on empty CTEs")

    return {
        "cte_results": cte_results,
        "empty_ctes": empty_ctes,
        "created_views": created_views,
        "final_count": final_count,
    }
