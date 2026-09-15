"""Threshold tuning service — the standalone Threshold Tuning workflow.

Reads a log file's scenario metadata, sets the OFSAA batch date to match it,
executes the scenario's full dataset query for a real alert count, and
returns the scenario's currently configured KDD_TSHLD threshold values.

The batch-date step matters for correctness, not just parity with the main
pipeline: this codebase's dataset queries commonly filter on the OFSAA
calendar (e.g. "TRXN_EXCTN_DT >= (SELECT Min_Dt FROM clndr_vw)", where
clndr_vw resolves off KDD_CAL's CLNDR_DAY_AGE=0 pointer) — skipping it would
execute the dataset query against whatever business date the OFSAA server
happens to have set from an unrelated prior run, producing a meaningless
alert count. This does mean the alert-count step carries the same SSH round
trip (~30-40s) as the main debugging pipeline's set_batch_date step; the
threshold-values-only lookup alone stays fast.

Reuses the exact same step functions the main 6-step pipeline uses
(orchestrator._step_log_reader, ._step_set_batch_date) rather than
duplicating their output-directory/metadata-file/query-extraction logic —
same state dict contract, same on-disk artifacts.
"""

import os
import re
import sys
import logging

from app.config import PIPELINE_DIR
from app.pipeline.orchestrator import _step_log_reader, _step_set_batch_date
from app.pipeline.db_connect import connect_to_oracle
from app.pipeline.sql_diagnostics import (
    get_threshold_config,
    extract_param_column_bindings,
    extract_alias_source_map,
    sample_column_stats,
    compute_threshold_kill_suggestion,
)

logger = logging.getLogger(__name__)

sys.path.insert(0, str(PIPELINE_DIR))

_NO_UNIT_SENTINELS = ("no_unit", "none", "n/a", "na", "null")


def _clean_unit(unit) -> str | None:
    """KDD_TSHLD.UNIT_TX literally contains placeholder strings like
    '<NO_UNIT>' for unitless thresholds — same fix already applied to the AI
    recommendation text (ai_recommendation_service.py's _clean_unit);
    replicated here since threshold values are displayed directly, not
    through that module."""
    cleaned = str(unit).strip().strip("<>").lower() if unit else ""
    if not cleaned or cleaned in _NO_UNIT_SENTINELS or "no_unit" in cleaned:
        return None
    return str(unit)


def _run_dataset_query_count(output_dir: str) -> int:
    """Like orchestrator._step_sql_executer, but returns the real alert
    COUNT rather than just a found-any/found-none boolean (execute_dataset_
    query only ever fetches 10 rows to answer that boolean — not enough for
    'how many alerts')."""
    from sql_executer import load_sql_file

    extracted_dir = os.path.join(output_dir, "extracted_queries")
    dataset_files = [
        os.path.join(extracted_dir, f)
        for f in os.listdir(extracted_dir)
        if "dataset_query" in f.lower() and f.lower().endswith(".sql")
    ] if os.path.isdir(extracted_dir) else []

    if not dataset_files:
        raise ValueError(f"No dataset query file found in {extracted_dir}")
    sql_file = max(dataset_files, key=os.path.getmtime)
    sql_query = load_sql_file(sql_file)

    conn = connect_to_oracle()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM (\n{sql_query}\n) t")
            return cursor.fetchone()[0]
        finally:
            cursor.close()
    finally:
        conn.close()


def get_current_thresholds(log_file_path: str) -> dict:
    """Returns {scenario_name, tshld_set_id, alert_count, thresholds: [...]}.

    Raises ValueError (caller maps to a 422) for expected failure cases: no
    queries found in the log, no threshold set found, or no thresholds
    configured for it.
    """
    state = {"log_file_path": log_file_path, "job_id": "", "metadata": {}, "output_dir": None, "run_logger": None}
    try:
        _step_log_reader(state)
    except Exception as e:
        raise ValueError(f"Could not read this log file: {e}")

    metadata = state["metadata"]
    scenario_name = metadata.get("job_description") or "UNKNOWN_SCENARIO"
    tshld_set_id = metadata.get("tshld_set_id")

    if not tshld_set_id:
        raise ValueError(
            "Could not find a threshold set (TSHLD_SET_ID) in this log file — "
            "make sure it's a full OFSAA scenario run log."
        )

    try:
        _step_set_batch_date(state)
    except Exception as e:
        raise ValueError(f"Could not set the OFSAA batch date for this scenario: {e}")

    alert_count = _run_dataset_query_count(state["output_dir"])

    conn = connect_to_oracle()
    try:
        config = get_threshold_config(conn, tshld_set_id)
    finally:
        conn.close()

    if not config:
        raise ValueError(f"No thresholds found in KDD_TSHLD for threshold set {tshld_set_id}.")

    thresholds = [
        {
            "name": tshld_name,
            "display_name": entry.get("display_name") or tshld_name,
            "current_value": entry.get("curr"),
            "min_value": entry.get("min"),
            "max_value": entry.get("max"),
            "unit": _clean_unit(entry.get("unit")),
            "description": entry.get("desc"),
        }
        for tshld_name, entry in config.items()
    ]
    thresholds.sort(key=lambda t: t["display_name"])

    logger.info(
        "Threshold tuning analysis: scenario=%s tshld_set_id=%s alert_count=%d thresholds=%d",
        scenario_name, tshld_set_id, alert_count, len(thresholds),
    )

    return {
        "scenario_name": scenario_name,
        "tshld_set_id": str(tshld_set_id),
        "alert_count": alert_count,
        "thresholds": thresholds,
    }


def _parse_float(v):
    try:
        return float(str(v).strip("'\""))
    except (TypeError, ValueError):
        return None


def _current_value_as_float(entry: dict):
    return _parse_float(entry.get("curr"))


# The admin-configured [min,max] range — clamped further by the real
# population's own min/max, since tightening a threshold past what any real
# row ever exhibits has no further effect and is a meaningless number to
# propose — is the outer search boundary used when tuning a threshold to
# REDUCE alert volume (see _legal_ceiling / get_threshold_recommendations).
# This is deliberately a different objective from
# compute_threshold_kill_suggestion (which answers "this threshold
# currently admits zero real data — what's the loosest value that admits
# any at all"): tuning for volume searches for a *calibrated* point inside
# that legal range — either the smallest change that hits a caller-supplied
# target reduction, or (with no target) the largest change that still
# leaves at least one real alert — never just jumping straight to the
# ceiling itself the way the failure-recovery case does.


def _legal_ceiling(current: float, op: str, entry: dict, stats: dict):
    """The most aggressive value this threshold could legally/meaningfully
    be tuned to: the admin-configured max/min, further bounded by the real
    population's own max/min (going past it excludes nothing more, so
    there's no reason to propose a value beyond it). Returns None if no
    such bound exists or it's already on the wrong side of `current`
    (nothing left to tune)."""
    if op in (">=", ">"):
        bounds = [v for v in (_parse_float(entry.get("max")), stats.get("actual_max")) if v is not None]
        if not bounds:
            return None
        ceiling = min(bounds)
        if ceiling <= current:
            return None
    else:  # '<=', '<'
        bounds = [v for v in (_parse_float(entry.get("min")), stats.get("actual_min")) if v is not None]
        if not bounds:
            return None
        ceiling = max(bounds)
        if ceiling >= current:
            return None
    return round(ceiling, 2)


_WS_RUN_RE = re.compile(r'\s+')


def _flex_ws_pattern(text: str) -> str:
    """Turns literal text into a regex matching it exactly except that each
    run of whitespace may differ in length/kind. The reference-query and
    dataset-query dumps for the same underlying SQL are not byte-identical
    outside the @param substitutions — e.g. 'SELECT ot.CUST_SEQ_ID' in one
    vs 'SELECT  ot.CUST_SEQ_ID' (double space) in the other — real, observed
    OFSAA log formatting noise unrelated to any parameter value, so anchors
    built from this text need to tolerate it rather than fail alignment."""
    parts = _WS_RUN_RE.split(text)
    return r'\s+'.join(re.escape(p) for p in parts)


def _flex_find(haystack: str, needle: str, start: int):
    """Like str.find(needle, start), but whitespace-run-tolerant (see
    _flex_ws_pattern). Returns (match_start, match_end) or None."""
    m = re.compile(_flex_ws_pattern(needle)).search(haystack, start)
    return (m.start(), m.end()) if m else None


def _build_param_position_map(main_sql: str, dataset_sql: str):
    """Maps every '@ParamName' token in main_sql to the exact character span
    in dataset_sql that OFSAA's own substitution replaced it with — found by
    walking both strings in lockstep off the static (non-parameter) text
    between tokens, which is the same SQL body in both (modulo whitespace
    formatting noise — see _flex_ws_pattern) since dataset_query is produced
    purely by substituting @param tokens in place.

    This is what makes locating/replacing one specific threshold's literal
    unambiguous even when several different thresholds compare the exact
    same column with the exact same operator — this scenario's six
    risk-tier amount thresholds all do 'Tot_Trxn_Am_Cdt >= <value>',
    differing only by which WHERE-clause branch they're in, and with the
    real data currently making several of those values coincide (e.g. two
    different thresholds both happening to read 250000 right now). A plain
    text/regex search for 'column op value' can't tell those occurrences
    apart — it always finds whichever one appears first — but position
    derived from the untouched static text around each distinct @param
    token can, because it's anchored to which token the text came from, not
    what value it happens to hold.

    Returns {param_name: [(start, end), ...]} — spans into dataset_sql, in
    the order the param's tokens appear in main_sql — or None if the two
    texts don't align (should not normally happen; the caller must treat
    that as "can't safely locate anything" and skip re-execution rather
    than guess).
    """
    # Stray NUL bytes have been observed at EOF in these extracted .sql
    # files (an encoding/read artifact, never meaningful SQL content) —
    # strip them before anything else, or they break the final static
    # anchor's match.
    main_sql = main_sql.replace("\x00", "")
    dataset_sql = dataset_sql.replace("\x00", "")

    # Both files are saved with a 2-line header ("-- REFERENCE QUERY" /
    # "-- DATASET QUERY", then "-- Extracted: <timestamp>") that differs
    # between the two even though the SQL body itself is aligned — strip it
    # so alignment starts from the actual query text, not the header.
    header_offset = 0
    header_m = re.match(r'^--[^\n]*\n--\s*Extracted:[^\n]*\n\n', dataset_sql, re.IGNORECASE)
    if header_m:
        header_offset = header_m.end()
        dataset_sql = dataset_sql[header_offset:]
    main_sql = re.sub(r'^--[^\n]*\n--\s*Extracted:[^\n]*\n\n', '', main_sql, count=1, flags=re.IGNORECASE)

    tokens = list(re.finditer(r'@(\w+)', main_sql))
    if not tokens:
        return {}

    pieces = []  # ("static", text) | ("param", name)
    prev_end = 0
    for m in tokens:
        pieces.append(("static", main_sql[prev_end:m.start()]))
        pieces.append(("param", m.group(1)))
        prev_end = m.end()
    pieces.append(("static", main_sql[prev_end:]))

    occurrences: dict = {}
    cursor = 0
    i = 0
    n = len(pieces)
    while i < n:
        kind, val = pieces[i]
        if kind == "static":
            if val:
                found = _flex_find(dataset_sql, val, cursor)
                if found is None:
                    return None
                cursor = found[1]
            i += 1
            continue

        # kind == "param": its literal runs from `cursor` up to wherever the
        # next static piece begins in dataset_sql.
        if i + 1 >= n:
            return None
        _next_kind, next_val = pieces[i + 1]
        if not next_val:
            return None  # two params with nothing between them — can't tell where this one's literal ends
        found = _flex_find(dataset_sql, next_val, cursor)
        if found is None:
            return None
        end_pos = found[0]
        occurrences.setdefault(val, []).append((cursor + header_offset, end_pos + header_offset))
        cursor = end_pos
        i += 1

    return occurrences


def _format_literal(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _fmt_num(value: float) -> str:
    return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"


def _execute_count(conn, sql_query: str) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM (\n{sql_query}\n) t")
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def _source_key(source):
    """A hashable key for a resolved source (bare table string, or a
    {"subquery": ...} dict) — used to group thresholds that can be sampled
    from the exact same real population in one query."""
    return source["subquery"] if isinstance(source, dict) else source


def _fetch_per_row_values(conn, source, exprs: dict):
    """Fetches every real population row's value for each of `exprs`
    ({name: sql_expression}) in one query. Returns a list of {name: value}
    dicts, one per real row, or [] on any failure (never raises — callers
    must treat that as "no per-row data available", not fail the request).

    Needed because loosening each threshold independently toward the whole
    population's own worst case can pick values that admit DIFFERENT real
    rows on different conditions, never converging on one row that clears
    every condition at once — confirmed live: one real record's transaction
    amount only cleared a Min-Amount threshold because a *different*
    record's higher amount set the bar, while that same record failed a
    completely different Min-Percentage threshold for the same reason in
    reverse. Per-row data lets a candidate be calibrated against one actual
    record's own values across all of its conditions together."""
    if not exprs:
        return []
    names = list(exprs.keys())
    select_list = ", ".join(f"({exprs[n]}) AS c{i}" for i, n in enumerate(names))
    from_clause = f"(\n{source['subquery']}\n) x_src" if isinstance(source, dict) else source
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT {select_list} FROM {from_clause}")
        return [dict(zip(names, record)) for record in cursor.fetchall()]
    except Exception as e:
        logger.debug("Per-row value fetch failed: %s", e)
        return []
    finally:
        cursor.close()


def _apply_recommendations_to_query(dataset_sql: str, param_occurrences, config: dict, recommendations: dict):
    """Substitutes each resolved recommendation's current literal value for
    its recommended one directly in the already-literal-substituted dataset
    query (the exact SQL OFSAA executed), using `param_occurrences` (see
    _build_param_position_map) to find exactly the span each threshold's own
    parameter token maps to — never a text/column search, which can't
    disambiguate distinct thresholds that share a column+operator+value.

    Returns (modified_sql, applied_changes, skipped_changes).
    """
    applied = []
    skipped = []

    if param_occurrences is None:
        return dataset_sql, applied, [
            {"name": name, "reason": "could not align the scenario SQL to locate parameter positions"}
            for name, rec in recommendations.items() if rec.get("recommended_value") is not None
        ]

    edits = []  # (start, end, new_literal)

    for tshld_name, rec in recommendations.items():
        new_value = rec.get("recommended_value")
        if new_value is None:
            continue

        current = rec.get("current_value")
        if current is None:
            skipped.append({"name": tshld_name, "reason": "current value could not be resolved"})
            continue

        if new_value == current:
            continue  # no actual change proposed — nothing to apply

        spans = param_occurrences.get(tshld_name)
        if not spans:
            skipped.append({"name": tshld_name, "reason": "parameter position not found in scenario SQL"})
            continue

        new_literal = _format_literal(new_value)
        for start, end in spans:
            edits.append((start, end, new_literal))

        entry = config.get(tshld_name) or {}
        applied.append({
            "name": tshld_name,
            "display_name": entry.get("display_name") or tshld_name,
            "current_value": current,
            "recommended_value": new_value,
        })

    if not edits:
        return dataset_sql, applied, skipped

    # Apply back-to-front so earlier spans' offsets stay valid as the string changes length.
    edits.sort(key=lambda e: e[0], reverse=True)
    modified = dataset_sql
    for start, end, new_literal in edits:
        modified = modified[:start] + new_literal + modified[end:]

    return modified, applied, skipped


def get_threshold_recommendations(log_file_path: str, target_reduction_pct: float | None = None) -> dict:
    """For each configured threshold, tries to resolve the real SQL column
    it's compared against (via the main SQL's 'col OP @param' bindings —
    param names exactly match KDD_TSHLD.TSHLD_NM, same mechanism already
    used by the failure-diagnosis path) and samples that column's real
    distribution.

    Which objective applies is decided by whether the scenario currently
    generates any alerts at all — the two cases need opposite directions,
    not just different aggressiveness:

    - **Zero alerts today**: the scenario is dead, so every tunable
      threshold is LOOSENED toward the loosest value that still admits real
      data, using compute_threshold_kill_suggestion (the exact engine the
      THRESHOLD_KILL failure diagnosis already relies on — nice-rounded
      toward the real observed max/min, clamped to the admin-configured
      range). Only `target_reduction_pct` doesn't apply here (there's
      nothing to reduce from zero) — it's ignored in this mode.
    - **Some alerts already**: each tunable threshold is TIGHTENED toward
      its _legal_ceiling (the admin-configured range, further bounded by
      the real population's own min/max), binary-searched by
      `target_reduction_pct`:
        - given (e.g. 30 for "cut alerts by ~30%"): finds the SMALLEST
          combined move that gets real alert volume down to at or below
          that target — the most conservative change that still hits the
          goal. If even the full legal ceiling can't reach it, returns the
          best achievable with a note explaining the configured range is
          the limiting factor (widening it is a business decision, not
          something this makes on its own).
        - not given: finds the LARGEST combined move that still leaves at
          least one real alert — "cut as much as legally possible without
          eliminating the scenario's output entirely".

    Both modes are verified the same way: real re-execution of the actual
    dataset query with every proposed change substituted in, never just the
    suggested numbers taken on faith.

    Every threshold gets an entry. Thresholds that can't be resolved to a
    single numeric column comparison (flags, lists, IDs, equality
    conditions, or ones whose table/column couldn't be identified) get
    recommended_value=None with a note explaining why — never a guessed
    number, consistent with this feature's whole design principle.

    Only recommendations whose parameter position can be located in the
    query count as "applied"; anything that can't be safely located is
    listed in skipped_changes rather than guessed.

    Returns {recommendations, current_alert_count, projected_alert_count,
    alert_count_delta, target_reduction_pct, target_met, applied_changes,
    skipped_changes}.
    """
    state = {"log_file_path": log_file_path, "job_id": "", "metadata": {}, "output_dir": None, "run_logger": None}
    try:
        _step_log_reader(state)
    except Exception as e:
        raise ValueError(f"Could not read this log file: {e}")

    metadata = state["metadata"]
    tshld_set_id = metadata.get("tshld_set_id")
    if not tshld_set_id:
        raise ValueError(
            "Could not find a threshold set (TSHLD_SET_ID) in this log file — "
            "make sure it's a full OFSAA scenario run log."
        )

    output_dir = state["output_dir"]
    extracted_dir = os.path.join(output_dir, "extracted_queries")

    def _latest(pattern: str) -> str:
        files = [
            os.path.join(extracted_dir, f)
            for f in os.listdir(extracted_dir)
            if pattern in f.lower() and f.lower().endswith(".sql")
        ] if os.path.isdir(extracted_dir) else []
        return open(max(files, key=os.path.getmtime), encoding="utf-8", errors="replace").read() if files else ""

    main_sql = _latest("main_query")
    dataset_sql = _latest("dataset_query")

    bindings = extract_param_column_bindings(main_sql)
    alias_source_map = extract_alias_source_map(dataset_sql or main_sql)
    param_occurrences = _build_param_position_map(main_sql, dataset_sql) if main_sql and dataset_sql else None

    conn = connect_to_oracle()
    try:
        config = get_threshold_config(conn, tshld_set_id)
        if not config:
            raise ValueError(f"No thresholds found in KDD_TSHLD for threshold set {tshld_set_id}.")

        recommendations = {}
        tunable = {}  # tshld_name -> {current, op, stats, entry, src_label} for thresholds eligible for percentile-based tuning

        for tshld_name, entry in config.items():
            cond = (bindings.get(tshld_name) or [None])[0]
            if not cond:
                recommendations[tshld_name] = {
                    "recommended_value": None,
                    "note": "Not compared against a single numeric column in the scenario SQL — no automatic recommendation.",
                }
                continue

            alias, col, op = cond
            if op == "=":
                recommendations[tshld_name] = {
                    "recommended_value": None,
                    "note": "Equality condition, not a tunable range — no automatic recommendation.",
                }
                continue

            if alias:
                source_candidates = [alias_source_map.get(alias.lower())]
            else:
                # No table prefix on this comparison (e.g. a HAVING-clause
                # condition written against the enclosing subquery's own
                # computed column, unambiguous in its own scope but not
                # recoverable from the SQL text alone) — try every known
                # distinct derived-query source in this dataset query.
                # Confirmed live this resolves real cases (a scenario's
                # "Names_Ct" threshold, computed in the same aggregation
                # subquery as sibling thresholds that do have an alias
                # prefix and already resolve fine) rather than giving up
                # immediately just because there's no prefix to look up.
                seen_sq = set()
                source_candidates = []
                for v in alias_source_map.values():
                    if isinstance(v, dict) and v["subquery"] not in seen_sq:
                        seen_sq.add(v["subquery"])
                        source_candidates.append(v)
            source_candidates = [s for s in source_candidates if s]
            if not source_candidates:
                recommendations[tshld_name] = {
                    "recommended_value": None,
                    "note": f"Could not resolve the source table for column '{col}'.",
                }
                continue

            current = None
            spans = (param_occurrences or {}).get(tshld_name)
            if spans:
                try:
                    current = float(dataset_sql[spans[0][0]:spans[0][1]].strip())
                except ValueError:
                    current = None
            if current is None:
                current = _current_value_as_float(entry)  # fallback: config, if the query has no literal for some reason
            if current is None:
                recommendations[tshld_name] = {
                    "recommended_value": None,
                    "note": "Current configured value isn't numeric.",
                }
                continue

            stats = None
            source = None
            for candidate in source_candidates:
                stats = sample_column_stats(conn, candidate, col)
                if stats:
                    source = candidate
                    break
            src_label = "derived query" if isinstance(source, dict) else (source or "any known source")
            if not stats:
                recommendations[tshld_name] = {
                    "recommended_value": None,
                    "current_value": current,
                    "note": f"Could not sample real data for {src_label}.{col}.",
                }
                continue

            tunable[tshld_name] = {
                "current": current, "op": op, "col": col, "stats": stats,
                "entry": entry, "src_label": src_label, "source": source,
            }

        if not dataset_sql:
            raise ValueError("No dataset query file found for this log — can't project alert impact.")

        current_alert_count = _execute_count(conn, dataset_sql)

        if current_alert_count == 0:
            # No alerts today — the opposite objective applies here: LOOSEN
            # each tunable threshold toward the loosest value that still
            # admits real data, using the exact same engine the
            # THRESHOLD_KILL failure diagnosis already relies on
            # (compute_threshold_kill_suggestion — nice-rounded toward the
            # real observed boundary, clamped to the admin-configured
            # range). The tightening/percentile-ceiling search below only
            # makes sense when there's already alert volume to trim; it
            # would be a no-op here since every candidate would already be
            # on the wrong side of "current" for that direction. Verified
            # the same way as the tighten path: real re-execution of the
            # actual dataset query with every proposed change substituted
            # in, never just the suggested numbers on their own.
            loosen_recs = {}
            blocking_names = []
            for name, info in tunable.items():
                stats = info["stats"]
                op = info["op"]
                current = info["current"]
                rows = stats["population_rows"]
                lower_bound = op in (">=", ">")
                actual_max = stats.get("actual_max")
                actual_min = stats.get("actual_min")

                # Only loosen a threshold that is ACTUALLY blocking the real
                # data — confirmed live this matters: a scenario commonly
                # pairs a Min and Max threshold on the same column (e.g. a
                # "small transaction amount" range meant to catch
                # structuring). Only the Min side may actually be failing;
                # the Max side can already be satisfied by every real row
                # (real max well under the configured cap). Blindly
                # "loosening" that Max threshold too — as if it were also a
                # blocker — used compute_threshold_kill_suggestion's
                # boundary-agnostic direction and produced a *lower* cap
                # than the real max, turning a condition that was already
                # passing into a new blocker and canceling out the correct
                # fix to the Min side. Net result: real re-execution still
                # showed 0 alerts despite "12 thresholds loosened" — every
                # one of those 12 looked like a fix in isolation, but half
                # of them were unrelated to the actual 0-alert cause and
                # actively made another condition worse.
                currently_blocking = (
                    (lower_bound and actual_max is not None and actual_max < current) or
                    (not lower_bound and actual_min is not None and actual_min > current)
                )
                if not currently_blocking:
                    boundary_label = "max" if lower_bound else "min"
                    boundary_value = actual_max if lower_bound else actual_min
                    loosen_recs[name] = {
                        "recommended_value": None,
                        "current_value": current,
                        "note": (
                            f"Already satisfied by the real data (observed {boundary_label}="
                            f"{boundary_value}, {rows:,} {info['src_label']} rows) — not a blocker here, "
                            f"no change needed."
                        ),
                    }
                    continue

                blocking_names.append(name)
                killer = {"column": info["col"], "operator": op, "threshold": current, **stats}
                calc = compute_threshold_kill_suggestion(killer, info["entry"])
                if calc["escalate"]:
                    loosen_recs[name] = {
                        "recommended_value": None,
                        "current_value": info["current"],
                        "note": (
                            f"No value in the configured allowed range ({calc['cfg_min']}-{calc['cfg_max']}) "
                            f"admits the real data (observed {'max' if calc['operator'] in ('>=', '>') else 'min'}"
                            f"={calc['boundary']}, {rows:,} {info['src_label']} rows) — the configured range "
                            f"itself would need to be widened to generate any alerts here."
                        ),
                    }
                elif calc["extreme_gap"]:
                    ratio = calc["gap_ratio"]
                    loosen_recs[name] = {
                        "recommended_value": calc["clamped_value"],
                        "current_value": info["current"],
                        "note": (
                            f"~{ratio:.1f}x gap vs real data ({rows:,} {info['src_label']} rows) — this "
                            f"environment's data may not represent production scale; verify with the business "
                            f"before applying."
                        ),
                    }
                else:
                    loosen_recs[name] = {
                        "recommended_value": calc["clamped_value"],
                        "current_value": info["current"],
                        "note": f"Loosened to admit real data — based on {rows:,} real {info['src_label']} rows.",
                    }

            recommendations.update(loosen_recs)
            modified_sql, applied_changes, skipped_changes = _apply_recommendations_to_query(
                dataset_sql, param_occurrences, config, loosen_recs
            )
            projected_alert_count = _execute_count(conn, modified_sql) if applied_changes else None
            target_met = None

            # The population-wide pass above loosens each threshold
            # independently toward the whole real candidate population's own
            # worst case — but different real rows can each satisfy
            # DIFFERENT conditions, never converging on any single row that
            # clears every one of them at once. Confirmed live: one real
            # record's transaction amount only cleared a Min-Amount
            # threshold because a *different* record's higher amount set the
            # rounding target, while that same record failed a completely
            # unrelated Min-Percentage threshold for the same reason in
            # reverse — neither record passed both. When the population-wide
            # pass still comes back at 0, fall back to targeting one
            # specific real row across every one of ITS blocking conditions
            # simultaneously, trying real rows in turn (bounded) until one's
            # own actual values, verified by real re-execution, produce an
            # alert.
            if applied_changes and projected_alert_count == 0 and blocking_names:
                groups: dict = {}
                for name in blocking_names:
                    groups.setdefault(_source_key(tunable[name]["source"]), []).append(name)

                for _key, group_names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                    source = tunable[group_names[0]]["source"]
                    exprs = {name: tunable[name]["col"] for name in group_names}
                    per_row = _fetch_per_row_values(conn, source, exprs)
                    if not per_row:
                        continue

                    succeeded = False
                    for row_values in per_row[:25]:  # bounded — real 0-alert candidate pools are typically small
                        row_recs = dict(loosen_recs)
                        for name in group_names:
                            info = tunable[name]
                            row_value = row_values.get(name)
                            if row_value is None:
                                row_recs = None
                                break
                            point_stats = dict(info["stats"])
                            point_stats["actual_max"] = row_value
                            point_stats["actual_min"] = row_value
                            killer = {"column": info["col"], "operator": info["op"], "threshold": info["current"], **point_stats}
                            calc = compute_threshold_kill_suggestion(killer, info["entry"])
                            if calc["clamped_value"] is None:
                                row_recs = None
                                break
                            row_recs[name] = {
                                "recommended_value": calc["clamped_value"],
                                "current_value": info["current"],
                                "note": (
                                    f"Calibrated to admit one specific real record's own value "
                                    f"({_fmt_num(row_value)}) — loosening every threshold toward the "
                                    f"population's own worst case (above) didn't converge on any single "
                                    f"real record that satisfies every condition simultaneously."
                                ),
                            }
                        if row_recs is None:
                            continue

                        trial_modified, trial_applied, trial_skipped = _apply_recommendations_to_query(
                            dataset_sql, param_occurrences, config, row_recs
                        )
                        if not trial_applied:
                            continue
                        trial_count = _execute_count(conn, trial_modified)
                        if trial_count > 0:
                            loosen_recs = row_recs
                            recommendations.update(row_recs)
                            modified_sql, applied_changes, skipped_changes = trial_modified, trial_applied, trial_skipped
                            projected_alert_count = trial_count
                            succeeded = True
                            logger.info(
                                "Threshold tuning (loosen): per-row targeting succeeded for tshld_set_id=%s "
                                "(group of %d thresholds) -> %d alerts",
                                tshld_set_id, len(group_names), trial_count,
                            )
                            break
                    if succeeded:
                        break

            alert_count_delta = (
                projected_alert_count - current_alert_count if projected_alert_count is not None else None
            )

            logger.info(
                "Threshold tuning (loosen): tshld_set_id=%s applied=%d skipped=%d current_alerts=0 projected_alerts=%s",
                tshld_set_id, len(applied_changes), len(skipped_changes), projected_alert_count,
            )

            return {
                "recommendations": recommendations,
                "current_alert_count": current_alert_count,
                "projected_alert_count": projected_alert_count,
                "alert_count_delta": alert_count_delta,
                "target_reduction_pct": None,
                "target_met": target_met,
                "applied_changes": applied_changes,
                "skipped_changes": skipped_changes,
            }

        # Outer search boundary for each tunable threshold: the full legal
        # ceiling (admin-configured range, further bounded by the real
        # population's own min/max — see _legal_ceiling), not a fixed
        # percentile. With a caller-supplied target we need the freedom to
        # search the whole legal range to actually reach it; with no
        # target, "largest safe move" should mean the true legal maximum,
        # not an arbitrary percentile short of it.
        ceilings = {}
        for name, info in tunable.items():
            ceiling = _legal_ceiling(info["current"], info["op"], info["entry"], info["stats"])
            if ceiling is not None:
                ceilings[name] = ceiling

        def _recs_at_fraction(t: float, target_note_suffix: str = "") -> dict:
            """Builds recommendations at interpolation fraction t between
            "no change" (t=0, current value) and "full legal ceiling" (t=1)
            for every tunable threshold at once. t=0 is a guaranteed floor:
            _apply_recommendations_to_query treats value==current as no-op,
            so the combined result is exactly today's real alert count —
            there is always a t with a real, verified, non-negative-alert
            outcome to fall back to."""
            recs = {}
            for name, info in tunable.items():
                ceiling = ceilings.get(name)
                rows = info["stats"]["population_rows"]
                if ceiling is None:
                    recs[name] = {
                        "recommended_value": None,
                        "current_value": info["current"],
                        "note": "No safe increase possible within the configured range — no automatic recommendation.",
                    }
                    continue

                value = round(info["current"] + t * (ceiling - info["current"]), 2)
                if value == info["current"]:
                    recs[name] = {
                        "recommended_value": None,
                        "current_value": info["current"],
                        "note": "No change needed here to reach the target." if target_note_suffix else
                                "No safe increase possible without eliminating every real alert currently generated.",
                    }
                elif t >= 0.999:
                    note = (
                        f"Set to the configured maximum/minimum of {_fmt_num(ceiling)} "
                        f"({rows:,} real {info['src_label']} rows sampled){target_note_suffix}"
                    )
                    recs[name] = {"recommended_value": ceiling, "current_value": info["current"], "note": note}
                else:
                    note = (
                        f"Raised {round(t * 100)}% of the way toward the configured maximum/minimum of "
                        f"{_fmt_num(ceiling)} ({rows:,} real {info['src_label']} rows sampled){target_note_suffix}"
                    )
                    recs[name] = {"recommended_value": value, "current_value": info["current"], "note": note}
            return recs

        def _trial(t: float, note_suffix: str = ""):
            recs = _recs_at_fraction(t, note_suffix)
            modified, applied, skipped = _apply_recommendations_to_query(dataset_sql, param_occurrences, config, recs)
            count = _execute_count(conn, modified) if applied else current_alert_count
            return recs, modified, applied, skipped, count

        target_count = None
        if target_reduction_pct is not None:
            pct = max(0.0, min(100.0, float(target_reduction_pct)))
            target_count = 0 if pct >= 100 else max(1, round(current_alert_count * (1 - pct / 100)))

        target_already_met = target_count is not None and target_count >= current_alert_count

        recs_full, modified_full, applied_full, skipped_full, count_full = _trial(1.0)
        target_met = None

        if target_already_met:
            # e.g. a 0%-ish target — current alert volume already satisfies
            # it, so no changes are proposed at all (distinct from "no
            # target given", which instead maximizes the safe cut).
            recommendations.update(_recs_at_fraction(0.0))
            modified_sql, applied_changes, skipped_changes, projected_alert_count = (
                dataset_sql, [], [], current_alert_count
            )
            target_met = True

        elif not applied_full:
            recommendations.update(recs_full)
            modified_sql, applied_changes, skipped_changes, projected_alert_count = dataset_sql, [], [], None

        elif target_count is None:
            # No target (or already met at t=0): find the LARGEST combined
            # move that still leaves at least one real alert. t=0's count
            # (current_alert_count > 0) is the guaranteed pre-loop floor —
            # this always converges to a real, non-zero, verified result
            # rather than ever landing on zero (confirmed live: a fixed
            # percentile rung could still zero out a small scenario when
            # many thresholds' changes compounded together).
            if count_full > 0:
                recommendations.update(recs_full)
                modified_sql, applied_changes, skipped_changes, projected_alert_count = (
                    modified_full, applied_full, skipped_full, count_full
                )
            else:
                lo, hi = 0.0, 1.0
                best = (dataset_sql, [], [], current_alert_count, {})  # t=0 floor
                for _ in range(5):  # ~3% resolution — enough precision without excessive re-executions
                    mid = (lo + hi) / 2
                    recs_mid, modified_mid, applied_mid, skipped_mid, count_mid = _trial(mid)
                    if count_mid > 0:
                        lo = mid
                        best = (modified_mid, applied_mid, skipped_mid, count_mid, recs_mid)
                    else:
                        hi = mid
                modified_sql, applied_changes, skipped_changes, projected_alert_count, best_recs = best
                recommendations.update(best_recs if best_recs else _recs_at_fraction(0.0))
                logger.info(
                    "Threshold tuning: full legal-range move zeroed out tshld_set_id=%s (%d alerts today) — "
                    "binary search settled on a partial move giving %s alerts",
                    tshld_set_id, current_alert_count, projected_alert_count,
                )

        else:
            # Explicit target: find the SMALLEST combined move that gets
            # real alert volume down to at or below target_count — the most
            # conservative change that still hits the goal, verified by
            # real re-execution at each step tried (never estimated).
            suffix = f" — reaching your {round(pct)}% reduction target."
            if count_full > target_count:
                # Even the full legal ceiling can't reach it — report the
                # best achievable, honestly labeled as short of the target.
                recs_full = _recs_at_fraction(1.0, " — the most this can be tuned; short of your target (see above).")
                modified_full, applied_full, skipped_full = _apply_recommendations_to_query(
                    dataset_sql, param_occurrences, config, recs_full
                )
                recommendations.update(recs_full)
                modified_sql, applied_changes, skipped_changes, projected_alert_count = (
                    modified_full, applied_full, skipped_full, count_full
                )
                target_met = False
                logger.info(
                    "Threshold tuning: target_count=%d unreachable for tshld_set_id=%s even at the full legal "
                    "ceiling (best=%d alerts)", target_count, tshld_set_id, count_full,
                )
            else:
                lo, hi = 0.0, 1.0
                best = (modified_full, applied_full, skipped_full, count_full, _recs_at_fraction(1.0, suffix))
                for _ in range(6):  # ~1.5% resolution
                    mid = (lo + hi) / 2
                    recs_mid, modified_mid, applied_mid, skipped_mid, count_mid = _trial(mid, suffix)
                    if count_mid <= target_count:
                        hi = mid
                        best = (modified_mid, applied_mid, skipped_mid, count_mid, recs_mid)
                    else:
                        lo = mid
                modified_sql, applied_changes, skipped_changes, projected_alert_count, best_recs = best
                recommendations.update(best_recs)
                target_met = True
                logger.info(
                    "Threshold tuning: target_count=%d reached for tshld_set_id=%s (%d alerts today) -> %d alerts",
                    target_count, tshld_set_id, current_alert_count, projected_alert_count,
                )

        alert_count_delta = projected_alert_count - current_alert_count if projected_alert_count is not None else None

        logger.info(
            "Threshold tuning recommend: tshld_set_id=%s target_pct=%s applied=%d skipped=%d current_alerts=%s "
            "projected_alerts=%s",
            tshld_set_id, target_reduction_pct, len(applied_changes), len(skipped_changes), current_alert_count,
            projected_alert_count,
        )

        return {
            "recommendations": recommendations,
            "current_alert_count": current_alert_count,
            "projected_alert_count": projected_alert_count,
            "alert_count_delta": alert_count_delta,
            "target_reduction_pct": target_reduction_pct,
            "target_met": target_met,
            "applied_changes": applied_changes,
            "skipped_changes": skipped_changes,
        }
    finally:
        conn.close()
