"""Pipeline orchestrator — runs the 6-step OFSAA diagnostic pipeline."""

import os
import re
import sys
import json
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from app.config import OUTPUTS_DIR, PIPELINE_DIR, PIPELINE_TIMEOUT_SECONDS
from app.database.job_repo import update_job_status, update_step_status
from app.database.audit_repo import audit
from app.services.ai_recommendation_service import generate_recommendation

logger = logging.getLogger(__name__)

sys.path.insert(0, str(PIPELINE_DIR))


async def _emit(queue, event: str, data: dict):
    message = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
    await queue.put(message)


def _step_label(name: str) -> str:
    return {
        "log_reader": "Log Reader",
        "set_batch_date": "Set Batch Date",
        "sql_executer": "SQL Executer",
        "cte_parser": "CTE Parser",
        "cte_executer": "CTE Executer",
        "sql_diagnostics": "SQL Diagnostics",
    }.get(name, name)


# ----------------------------------------------------------------------
# STEP FUNCTIONS
# ----------------------------------------------------------------------

def _step_log_reader(state: dict) -> str:
    from log_reader import (
        _read_uploaded_text,
        _extract_log_metadata,
        _extract_all_queries_from_log,
        _save_queries_to_files,
        _save_metadata,
        _is_function_scenario,
    )
    from path_manager import create_output_directory

    log_file_path = state["log_file_path"]
    log_text = _read_uploaded_text(log_file_path)
    metadata = _extract_log_metadata(log_text)
    state["metadata"] = metadata

    multi_query = _is_function_scenario(metadata)
    state["multi_query"] = multi_query
    if multi_query:
        print("\n  Function scenario detected — using multi-query flow")
    else:
        print("\n  Standard scenario — using single-query flow")

    scenario_name = metadata.get("job_description", "UNKNOWN_SCENARIO")
    output_dir = create_output_directory(scenario_name)
    state["output_dir"] = output_dir

    _save_metadata(metadata, output_dir)
    queries = _extract_all_queries_from_log(log_text, multi_query=multi_query)

    if queries["count"] == 0:
        raise ValueError("No SQL queries found in log file")

    from db_connect import connect_to_oracle
    func_conn = None
    try:
        if multi_query:
            func_conn = connect_to_oracle()
        _save_queries_to_files(queries, metadata, db_conn=func_conn, multi_query=multi_query)
    finally:
        if func_conn:
            func_conn.close()

    update_job_status(
        state.get("job_id", ""),
        "running",
        scenario_name=scenario_name,
        batch_date=str(metadata.get("current_business_date", "")),
        output_dir=output_dir,
    )

    return f"Scenario: {scenario_name} | Date: {metadata.get('current_business_date')} | Queries extracted: {queries['count']}"


def _step_execute_resolved_function(state: dict) -> str:
    import json as _json
    import re as _re
    from db_connect import connect_to_oracle
    from sql_executer import execute_dataset_query

    output_dir = state["output_dir"]
    extracted_dir = os.path.join(output_dir, "extracted_queries")
    run_logger = state.get("run_logger")

    if not os.path.isdir(extracted_dir):
        raise FileNotFoundError(f"Extracted queries directory not found: {extracted_dir}")

    func_files = [
        f for f in os.listdir(extracted_dir)
        if f.lower().endswith(".sql") and (f.upper().startswith("F_") or f.upper().startswith("ANOM"))
    ]
    if not func_files:
        raise FileNotFoundError(f"No function SQL files (F_*.sql or ANOM*.sql) found in {extracted_dir}")

    param_files = [
        f for f in os.listdir(extracted_dir)
        if f.startswith("parameters_") and f.endswith(".json")
    ]
    if not param_files:
        raise FileNotFoundError(f"No parameters_*.json found in {extracted_dir}")

    func_path = os.path.join(extracted_dir, max(func_files, key=lambda f: os.path.getmtime(os.path.join(extracted_dir, f))))
    param_path = os.path.join(extracted_dir, max(param_files, key=lambda f: os.path.getmtime(os.path.join(extracted_dir, f))))

    with open(func_path, "r", encoding="utf-8") as f:
        func_sql = f.read()

    with open(param_path, "r", encoding="utf-8") as f:
        params = _json.load(f)

    print(f"\nLoaded function: {func_path}")
    print(f"Loaded parameters: {param_path} ({len(params)} params)")

    start_match = _re.search(r'\bIS\s+', func_sql, _re.IGNORECASE)
    end_match = _re.search(r'^\s*BEGIN\b', func_sql, _re.MULTILINE)

    if not start_match:
        raise ValueError("Could not find IS in function body")
    if not end_match or end_match.start() <= start_match.end():
        raise ValueError("Could not find BEGIN in function body")

    extracted_sql = func_sql[start_match.end():end_match.start()].strip()
    print(f"\n  Extracted SQL: {len(extracted_sql)} chars")

    # ---- Self-Discovering Parameter Matcher ----
    # Step 1: Extract SQL param names from function signature
    sig_match = _re.search(r'(?i)FUNCTION\s+\w+\s*\((.*?)\)\s+RETURN', func_sql, _re.DOTALL)
    if not sig_match:
        sig_match = _re.search(r'(?i)FUNCTION\s+\w+\s*\((.*?)\)\s+IS', func_sql, _re.DOTALL)
    if not sig_match:
        sig_match = _re.search(r'(?i)PROCEDURE\s+\w+\s*\((.*?)\)', func_sql, _re.DOTALL)

    sql_params = []
    if sig_match:
        sig_text = sig_match.group(1)
        sql_params = _re.findall(r'\b(p_\w+)\b', sig_text, _re.IGNORECASE)
        print(f"\n  Found {len(sql_params)} SQL params in signature: {', '.join(sql_params)}")
    else:
        sql_params = _re.findall(r'\b(p_\w+)\b', extracted_sql, _re.IGNORECASE)
        sql_params = list(set(sql_params))
        print(f"\n  Found {len(sql_params)} SQL params in body: {', '.join(sorted(sql_params))}")

    # Step 2: Normalize function
    _ABBREV_TO_FULL = {
        "csh": "cash", "trxn": "transaction", "prdct": "product", "lst": "list",
        "incl": "include", "mi": "monthly", "tp": "type", "cd": "code",
        "dt": "date", "tot": "total", "cnt": "count", "val": "value",
        "ind": "indicator", "cat": "category", "freq": "frequency",
        "bal": "balance", "opng": "opening", "clsg": "closing",
        "avg": "average", "min": "minimum", "max": "maximum",
        "amt": "amount", "pct": "percentage", "num": "number",
        "dly": "daily", "seq": "sequential", "acct": "account",
        "desc": "description", "tshld": "threshold", "bus": "business",
        "cust": "customer", "ref": "reference", "jrsd": "jurisdiction",
        "effctv": "effective", "prd": "period", "mvmt": "movement",
        "rpd": "rapid", "fnd": "funds", "actvty": "activity",
        "prfl": "profile", "expctd": "expected", "trrst": "terrorist",
        "fncng": "financing", "anom": "anomaly", "strctrng": "structuring",
        "avd": "avoid", "rpt": "report", "alrt": "alert",
        "ent": "entity", "grp": "group", "brnch": "branch",
        "seg": "segment", "chnl": "channel", "rgn": "region",
        "cntry": "country", "incld": "included", "fl": "flag",
        "trans": "transaction", "transfr": "transfer", "b2b": "business_to_business",
    }

    def _normalize(name):
        n = name.lower().strip()
        if n.startswith("p_"):
            n = n[2:]
        for abbr, full in sorted(_ABBREV_TO_FULL.items(), key=lambda x: -len(x[0])):
            n = _re.sub(r'\b' + _re.escape(abbr) + r'\b', full, n)
        # Strip common filler words that may exist on one side but not the other
        for filler in ["transaction", "transfer", "flag", "list", "type"]:
            n = n.replace(filler, "")
        n = n.replace("_", "")
        return n

    def _similarity(a, b):
        if not a or not b:
            return 0
        if a == b:
            return 1.0
        if a in b or b in a:
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            return shorter / longer
        m, n = len(a), len(b)
        if m == 0 or n == 0:
            return 0
        dp = [[0] * (n + 1) for _ in range(2)]
        for j in range(n + 1):
            dp[0][j] = j
        for i in range(1, m + 1):
            curr = i % 2
            prev = (i - 1) % 2
            dp[curr][0] = i
            for j in range(1, n + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                dp[curr][j] = min(dp[prev][j] + 1, dp[curr][j-1] + 1, dp[prev][j-1] + cost)
        dist = dp[m % 2][n]
        return 1 - dist / max(m, n)

    sql_param_map = {}
    for sp in sql_params:
        sql_param_map[_normalize(sp)] = sp

    print(f"\n  SQL param map (normalized -> actual):")
    for k, v in sorted(sql_param_map.items()):
        print(f"    {k} -> {v}")

    # Step 3: Match each JSON key to a SQL param and replace
    resolved_sql = extracted_sql
    matched_count = 0
    unmatched_keys = []

    for json_key, json_value in params.items():
        norm_key = _normalize(json_key)
        matched_sql_param = sql_param_map.get(norm_key)

        if not matched_sql_param:
            best_match = None
            best_score = 0
            for norm_sp, actual_sp in sql_param_map.items():
                score = _similarity(norm_key, norm_sp)
                if score > best_score:
                    best_score = score
                    best_match = actual_sp
            if best_score > 0.6:
                matched_sql_param = best_match
                print(f"    Fuzzy match ({best_score:.2f}): {json_key} -> {matched_sql_param}")

        if matched_sql_param:
            pattern = _re.compile(r'\b' + _re.escape(matched_sql_param) + r'\b', _re.IGNORECASE)
            resolved_sql = pattern.sub(str(json_value), resolved_sql)
            matched_count += 1
        else:
            unmatched_keys.append(json_key)

    print(f"\n  Matched: {matched_count}/{len(params)} params")
    if unmatched_keys:
        print(f"  Unmatched: {', '.join(unmatched_keys)}")

    resolved_sql = resolved_sql.rstrip(";").strip()
    state["resolved_function_sql"] = resolved_sql

    print(f"  Resolved SQL: {len(resolved_sql)} chars")

    if run_logger:
        run_logger.log_sql(3, "resolved_function", resolved_sql)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_file = os.path.join(extracted_dir, f"resolved_function_debug_{timestamp}.sql")
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write("-- RESOLVED FUNCTION SQL (DEBUG)\n")
        f.write(f"-- Extracted: {datetime.now()}\n\n")
        f.write(resolved_sql)
    print(f"  Saved resolved SQL for debug: {debug_file}")

    conn = None
    try:
        conn = connect_to_oracle()
        has_alerts = execute_dataset_query(conn, resolved_sql, run_logger=run_logger)
        state["has_alerts"] = has_alerts

        if has_alerts:
            return "Resolved function executed - alerts generated. Pipeline complete."
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ds_file = os.path.join(extracted_dir, f"resolved_function_dataset_{timestamp}.sql")
            with open(ds_file, "w", encoding="utf-8") as f:
                f.write("-- RESOLVED FUNCTION SQL\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(resolved_sql)
            print(f"\n  Saved resolved function as dataset:\n  {ds_file}")
            state["resolved_function_sql_file"] = ds_file
            return "Resolved function executed - no alerts. Proceeding to CTE analysis."
    finally:
        if conn:
            conn.close()


def _step_set_batch_date(state: dict) -> str:
    from set_batch_date import set_batch_date

    # Pass this job's own output_dir explicitly — set_batch_date's
    # load_metadata() defaults to "whatever directory is most recently
    # modified anywhere under OUTPUT_BASE_PATH" when not given one, which
    # is a real race under concurrency (confirmed live: this app doesn't
    # serialize requests, and any other job/background test touching a
    # different scenario's directory even a moment earlier could make this
    # step read the wrong job's metadata, or hit a bare "Metadata file not
    # found" with no connection to the file this job actually uploaded).
    set_batch_date(state["output_dir"])

    business_date = state["metadata"].get("current_business_date", "unknown")
    return f"Batch date set to: {business_date}"


def _step_sql_executer(state: dict) -> str:
    from sql_executer import load_sql_file, execute_dataset_query
    from db_connect import connect_to_oracle

    run_logger = state.get("run_logger")

    output_dir = state["output_dir"]
    extracted_dir = os.path.join(output_dir, "extracted_queries")
    dataset_files = [
        os.path.join(extracted_dir, f)
        for f in os.listdir(extracted_dir)
        if "dataset_query" in f.lower() and f.lower().endswith(".sql")
    ] if os.path.isdir(extracted_dir) else []

    if not dataset_files:
        raise FileNotFoundError(f"No dataset query file found in {extracted_dir}")
    sql_file = max(dataset_files, key=os.path.getmtime)

    conn = None
    try:
        conn = connect_to_oracle()
        sql_query = load_sql_file(sql_file)
        has_alerts = execute_dataset_query(conn, sql_query, run_logger=run_logger)

        state["has_alerts"] = has_alerts

        if has_alerts:
            return "Alerts generated - scenario is working correctly"
        else:
            return "No alerts generated - proceeding to CTE analysis"
    finally:
        if conn:
            conn.close()


def _step_cte_parser(state: dict) -> str:
    import importlib.util
    cte_parser_path = PIPELINE_DIR / "cte_parser.py"

    spec = importlib.util.spec_from_file_location("cte_parser", cte_parser_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output_dir = state["output_dir"]
    run_logger = state.get("run_logger")
    if hasattr(module, "parse_ctes"):
        module.parse_ctes(output_dir=output_dir, run_logger=run_logger)
    elif hasattr(module, "main"):
        module.main()

    parsed_dir = os.path.join(output_dir, "parsed_ctes")
    cte_count = len([
        f for f in os.listdir(parsed_dir)
        if f.endswith(".sql") and f != "final_query.sql"
    ]) if os.path.exists(parsed_dir) else 0

    return f"Parsed {cte_count} CTEs into {parsed_dir}"


def _step_cte_executer(state: dict) -> str:
    import json as _json
    import importlib.util
    from db_connect import connect_to_oracle

    cte_executer_path = PIPELINE_DIR / "cte_executer.py"

    spec = importlib.util.spec_from_file_location("cte_executer_mod", cte_executer_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output_dir = state["output_dir"]
    parsed_dir = os.path.join(output_dir, "parsed_ctes")
    run_logger = state.get("run_logger")

    metadata = module.load_metadata(output_dir)
    ctes = module.load_ctes(parsed_dir)
    final_query = module.load_final_query(parsed_dir)

    conn = None
    try:
        conn = connect_to_oracle()

        exec_result = module.execute_ctes(conn, ctes, final_query, metadata, run_logger=run_logger)

        state["cte_results"] = exec_result["cte_results"]
        state["empty_ctes"] = exec_result["empty_ctes"]
        state["failed_cte"] = exec_result["empty_ctes"][0] if exec_result["empty_ctes"] else None
        state["created_views"] = exec_result["created_views"]
        state["cte_metadata"] = metadata
        state["conn"] = conn
        state["final_query"] = final_query
        state["final_count"] = exec_result["final_count"]

        if exec_result["empty_ctes"]:
            empty_names = [c["name"] for c in exec_result["empty_ctes"]]
            return f"Empty CTEs found: {', '.join(empty_names)} - proceeding to diagnosis"
        else:
            return f"All {len(ctes)} CTEs returned rows - final query returned {exec_result['final_count']} rows"

    except Exception as e:
        if conn and "created_views" in state:
            module.drop_views(conn, state.get("created_views", []))
        raise


def _step_sql_diagnostics(state: dict) -> str:
    from sql_diagnostics import run_granular_cte_diagnostics
    from sql_executer import load_sql_file, get_latest_dataset_query_file
    import importlib.util

    empty_ctes = state.get("empty_ctes", [])
    failed_cte = state.get("failed_cte")
    conn = state.get("conn")
    metadata = dict(state.get("cte_metadata", {}))
    metadata["output_dir"] = state.get("output_dir", "")
    created_views = state.get("created_views", [])
    run_logger = state.get("run_logger")

    dataset_query_sql = None
    dataset_query_raw = None
    dataset_query_file = None
    try:
        dataset_query_file = get_latest_dataset_query_file()
        dataset_query_sql = load_sql_file(dataset_query_file)
        with open(dataset_query_file, "r", encoding="utf-8") as f:
            dataset_query_raw = f.read()
        logger.info("Loaded dataset query: %s (stripped=%d, raw=%d chars)", dataset_query_file, len(dataset_query_sql), len(dataset_query_raw))
    except Exception as e:
        logger.warning("Could not load dataset query for line number lookup: %s", e)

    cte_executer_path = PIPELINE_DIR / "cte_executer.py"
    spec = importlib.util.spec_from_file_location("cte_executer_mod", cte_executer_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        output_dir = metadata.get("output_dir", "")
        parsed_ctes_dir = os.path.join(output_dir, "parsed_ctes")
        has_ctes = False
        if os.path.isdir(parsed_ctes_dir):
            cte_files = [f for f in os.listdir(parsed_ctes_dir)
                         if f.endswith(".sql") and f != "final_query.sql"
                         and not f.startswith("000_")]
            has_ctes = len(cte_files) > 0

        ctes_to_diagnose = empty_ctes if empty_ctes else None

        if not ctes_to_diagnose:
            if not failed_cte:
                if not has_ctes:
                    if dataset_query_sql:
                        if run_logger:
                            run_logger.log(6, "No CTEs found - treating entire dataset query as final_query")
                        failed_cte = {"name": "final_query", "sql": dataset_query_sql}
                        if run_logger:
                            run_logger.log(6, f"Dataset query loaded for diagnosis ({len(dataset_query_sql)} chars)")
                    else:
                        return "No CTEs found and dataset query not available for diagnosis"
                else:
                    final_query = state.get("final_query", "")
                    if final_query:
                        failed_cte = {"name": "final_query", "sql": final_query}
                    else:
                        if dataset_query_sql:
                            failed_cte = {"name": "final_query", "sql": dataset_query_sql}
                            if run_logger:
                                run_logger.log(6, "No final_query extracted - using dataset query directly")
                        else:
                            return "No final query or dataset query available for diagnosis"
            ctes_to_diagnose = [failed_cte]

        if run_logger and len(ctes_to_diagnose) > 1:
            cte_names = [c["name"] for c in ctes_to_diagnose]
            run_logger.log(6, f"Diagnosing {len(ctes_to_diagnose)} empty CTEs: {', '.join(cte_names)}")

        results = run_granular_cte_diagnostics(
            conn, ctes_to_diagnose, metadata, run_logger=run_logger,
            dataset_query_sql=dataset_query_sql,
            dataset_query_raw=dataset_query_raw,
            dataset_query_file=dataset_query_file,
        )

        state["results"] = results

        root_cause_lines = []
        for r in results:
            line = (
                f"CTE '{r['cte_name']}' returns 0 rows. "
                f"Failure type: {r.get('failure_type', 'unknown').upper()}. "
                f"{r.get('likely_cause', '')}"
            )
            if r.get("failure_condition"):
                line += f" Condition: {r['failure_condition'][:200]}"
            if r.get("condition_line_number"):
                line += f" [Dataset query line {r['condition_line_number']}]"
            root_cause_lines.append(line)
        root_cause = "\n".join(root_cause_lines) if root_cause_lines else "Diagnosis inconclusive - could not pinpoint root cause"

        state["root_cause"] = root_cause

        detail_lines = []
        if results:
            if len(results) > 1:
                detail_lines.append(f"Diagnosed {len(results)} empty CTEs:")
                detail_lines.append("")
            for r in results:
                if len(results) > 1:
                    detail_lines.append(f"--- CTE: {r['cte_name']} ---")
                detail_lines.append(f"CTE: {r['cte_name']}")
                detail_lines.append(f"Failure Type: {r.get('failure_type', 'unknown').upper()}")
                if r.get("failure_condition"):
                    detail_lines.append(f"Killer Condition: {r['failure_condition']}")
                if r.get("condition_line_number"):
                    detail_lines.append(f"Dataset Query Line: {r['condition_line_number']}")
                if r.get("likely_cause"):
                    detail_lines.append(f"Likely Cause: {r['likely_cause']}")
                if r.get("rows_after") is not None:
                    detail_lines.append(f"Rows After Elimination: {r['rows_after']:,}")
                detail_lines.append("")
        else:
            detail_lines.append(root_cause)
        step_output = "\n".join(detail_lines)

        if run_logger and results:
            run_logger.section("ROOT CAUSE SUMMARY")
            for line in detail_lines:
                run_logger.log(6, line)

        return step_output

    finally:
        if conn and created_views:
            module.drop_views(conn, created_views)

        inner_views = []
        for r in state.get("results", []):
            inner_views.extend(r.get("_inner_query_views", []))
        if conn and inner_views:
            module.drop_views(conn, inner_views)

        if conn:
            conn.close()
            logger.info("Oracle connection closed after diagnostics")


# ----------------------------------------------------------------------
# STEP RUNNER & PIPELINE
# ----------------------------------------------------------------------

async def _run_step(job_id, step_name, queue, fn, state, next_step):
    logger.info("Step starting: %s", step_name)
    await _emit(queue, "step_started", {"job_id": job_id, "step": step_name, "message": f"Starting {_step_label(step_name)}..."})
    update_step_status(job_id, step_name, "running")
    update_job_status(job_id, "running", current_step=step_name)
    try:
        output = await asyncio.wait_for(
            asyncio.to_thread(fn, state),
            timeout=PIPELINE_TIMEOUT_SECONDS
        )
        await _emit(queue, "step_completed", {"job_id": job_id, "step": step_name, "message": f"{_step_label(step_name)} completed", "output": output})
        update_step_status(job_id, step_name, "completed", output=output)
        if next_step:
            update_job_status(job_id, "running", current_step=next_step)
        logger.info("Step completed: %s", step_name)
        return True
    except asyncio.TimeoutError:
        error_msg = f"Step timed out after {PIPELINE_TIMEOUT_SECONDS}s"
        logger.error("Step timed out: %s - %s", step_name, error_msg)
        await _emit(queue, "step_failed", {"job_id": job_id, "step": step_name, "message": f"{_step_label(step_name)} failed", "error": error_msg})
        update_step_status(job_id, step_name, "failed", error=error_msg)
        return False
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error("Step failed: %s - %s", step_name, error_msg)
        await _emit(queue, "step_failed", {"job_id": job_id, "step": step_name, "message": f"{_step_label(step_name)} failed", "error": error_msg})
        update_step_status(job_id, step_name, "failed", error=error_msg)
        return False


async def _fail_job(job_id, username, user_id, reason, queue, state=None):
    update_job_status(job_id, "failed", error_message=reason)
    await _emit(queue, "job_failed", {"job_id": job_id, "message": f"Pipeline stopped: {reason}"})
    audit("pipeline_failed", username=username, user_id=user_id, job_id=job_id, detail=reason)
    run_logger = state.get("run_logger") if state else None
    if run_logger:
        run_logger.close()
    logger.error("Pipeline failed - job_id=%s reason=%s", job_id, reason)


_STEP_FNS = {
    "log_reader": _step_log_reader,
    "set_batch_date": _step_set_batch_date,
    "sql_executer": _step_sql_executer,
    "execute_resolved_function": _step_execute_resolved_function,
    "cte_parser": _step_cte_parser,
    "cte_executer": _step_cte_executer,
    "sql_diagnostics": _step_sql_diagnostics,
}


async def run_pipeline(job_id, user_id, username, log_file_path, progress_queue):
    state = {
        "log_file_path": log_file_path,
        "job_id": job_id,
        "run_logger": None,
        "output_dir": None,
        "metadata": {},
        "has_alerts": False,
        "results": [],
    }
    try:
        # STEP 1 - LOG READER
        success = await _run_step(job_id, "log_reader", progress_queue, _STEP_FNS["log_reader"], state, "set_batch_date")
        if not success:
            await _fail_job(job_id, username, user_id, "log_reader failed", progress_queue, state)
            return

        from run_logger import RunLogger
        state["run_logger"] = RunLogger(
            output_dir=state["output_dir"],
            job_id=job_id,
            scenario_name=state["metadata"].get("job_description", "UNKNOWN_SCENARIO"),
        )

        # STEP 2 - SET BATCH DATE
        success = await _run_step(job_id, "set_batch_date", progress_queue, _STEP_FNS["set_batch_date"], state, "sql_executer")
        if not success:
            await _fail_job(job_id, username, user_id, "set_batch_date failed", progress_queue, state)
            return

        # ROUTE BASED ON SCENARIO TYPE
        if state.get("multi_query"):
            # FUNCTION SCENARIO FLOW
            success = await _run_step(job_id, "sql_executer", progress_queue, _STEP_FNS["execute_resolved_function"], state, None)
            if not success:
                await _fail_job(job_id, username, user_id, "resolved function execution failed", progress_queue, state)
                return

            if state.get("has_alerts"):
                update_job_status(job_id, "completed", alerts_generated=1, root_cause="Function executed - alerts generated")
                await _emit(progress_queue, "job_completed", {
                    "job_id": job_id, "alerts_generated": True,
                    "root_cause": "Function executed - alerts generated",
                })
                audit("pipeline_completed", username=username, user_id=user_id, job_id=job_id, detail="Function executed - alerts generated")
                state["run_logger"].close()
                logger.info("Pipeline completed - job_id=%s", job_id)
                return
            # else: no alerts -> fall through to Steps 4-6

        # STEP 3 - SQL EXECUTER (skip for function scenarios that already ran)
        if not state.get("multi_query"):
            success = await _run_step(job_id, "sql_executer", progress_queue, _STEP_FNS["sql_executer"], state, "cte_parser")
            if not success:
                await _fail_job(job_id, username, user_id, "sql_executer failed", progress_queue, state)
                return

            if state.get("has_alerts"):
                update_job_status(job_id, "completed", alerts_generated=1, root_cause="Alerts generated successfully - no diagnosis needed")
                await _emit(progress_queue, "job_completed", {
                    "job_id": job_id, "alerts_generated": True,
                    "root_cause": "Alerts generated successfully",
                })
                audit("pipeline_completed", username=username, user_id=user_id, job_id=job_id, detail="Alerts generated")
                state["run_logger"].close()
                logger.info("Pipeline completed - job_id=%s", job_id)
                return

        # STEP 4 - CTE PARSER
        success = await _run_step(job_id, "cte_parser", progress_queue, _STEP_FNS["cte_parser"], state, "cte_executer")
        if not success:
            await _fail_job(job_id, username, user_id, "cte_parser failed", progress_queue, state)
            return

        # STEP 5 - CTE EXECUTER
        success = await _run_step(job_id, "cte_executer", progress_queue, _STEP_FNS["cte_executer"], state, "sql_diagnostics")
        if not success:
            await _fail_job(job_id, username, user_id, "cte_executer failed", progress_queue, state)
            return

        # STEP 6 - SQL DIAGNOSTICS
        success = await _run_step(job_id, "sql_diagnostics", progress_queue, _STEP_FNS["sql_diagnostics"], state, None)
        if not success:
            await _fail_job(job_id, username, user_id, "sql_diagnostics failed", progress_queue, state)
            return

        root_cause = state.get("root_cause", "Unknown")
        results_json = json.dumps(state.get("results", []))
        cte_results_json = json.dumps(state.get("cte_results", []))

        ai_recommendation = await asyncio.to_thread(
            generate_recommendation, root_cause, state.get("results", [])
        )

        update_job_status(
            job_id, "completed", alerts_generated=0, root_cause=root_cause,
            result_json=results_json, cte_results_json=cte_results_json,
            ai_recommendation=ai_recommendation,
        )
        await _emit(progress_queue, "job_completed", {
            "job_id": job_id, "alerts_generated": False, "root_cause": root_cause,
            "results": state.get("results", []), "cte_results": state.get("cte_results", []),
            "ai_recommendation": ai_recommendation,
        })
        audit("pipeline_completed", username=username, user_id=user_id, job_id=job_id, detail=f"No alerts - root cause: {root_cause}")
        state["run_logger"].close()
        logger.info("Pipeline completed - job_id=%s", job_id)
    except Exception as e:
        await _fail_job(job_id, username, user_id, f"Unexpected error: {e}", progress_queue, state)
