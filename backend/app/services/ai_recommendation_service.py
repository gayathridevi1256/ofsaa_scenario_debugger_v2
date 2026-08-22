"""AI recommendation service — a short, actionable 2-line suggestion generated
from the diagnosed root cause, via a local Ollama server.

Local-only by design: no data leaves the box, no per-call API cost. This is a
best-effort enhancement — any failure (Ollama not running, timeout, bad
response, or leaked prompt text) is logged and swallowed so it never fails a
diagnostic job.
"""

import json
import logging
import re
import urllib.request
import urllib.error

from app.config import (
    AI_RECOMMENDATION_ENABLED,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_S,
)

logger = logging.getLogger(__name__)

_FORMAT_SUFFIX = (
    "\n\nReply with EXACTLY 2 short lines of plain text — no headings, no "
    "markdown, no bullet markers, no preamble like 'Here is...'. Be specific "
    "and concrete."
)

# Used when a real, DB-verified row count exists for at least one table (see
# "Measured facts" in the prompt). Deliberately gives no example sentence with
# placeholder letters — an earlier version of this prompt did, and a local 8B
# model echoed that literal example back as if it were real output the moment
# it had nothing else to ground on. Removing the leaky text from context when
# it's irrelevant is safer than instructing the model not to use it.
_SYSTEM_PROMPT_MEASURED = (
    "You are an assistant to an OFSAA/Mantas AML scenario analyst who is debugging "
    "why a scenario produced zero alerts. A 'Measured facts' section below lists "
    "table names with row counts that were already verified by directly querying "
    "the database — this is a confirmed result, not a hypothesis. Never invent, "
    "guess, or substitute any table name that is not explicitly listed in Measured "
    "facts or the root cause text above it."
    "\n\n"
    "Every table in Measured facts has 0 rows. State this as a fact, using the "
    "exact table name(s) already given — do not phrase it as something to check "
    "or verify, since that has already been done — and recommend loading/"
    "refreshing data into it/them. Only name tables that literally appear in "
    "Measured facts; if there is only one, name only that one. Never repeat a "
    "table name with a modified or extended suffix — each table name must be "
    "copied exactly as given, once."
    "\n\n"
    "If a fact gives a configured threshold's current value and description, "
    "explain briefly what it controls using that description, and recommend "
    "lowering the threshold's configured value. The analyst reading this "
    "cannot edit the scenario's SQL — never recommend reviewing, adjusting, "
    "or changing the condition/query itself, only loading data or changing a "
    "threshold's configured value."
    + _FORMAT_SUFFIX
)

# Used when at least one Measured fact shows rows already exist — a local 8B
# model reliably conflates "0 rows" and "rows exist" when asked to branch on
# the count *within* one prompt (confirmed live: it said "has no data" about a
# table with 5,000 rows). Precomputing the branch here and using a dedicated
# prompt removes that reasoning step from the model entirely.
_SYSTEM_PROMPT_MEASURED_NONZERO = (
    "You are an assistant to an OFSAA/Mantas AML scenario analyst who is debugging "
    "why a scenario produced zero alerts. A 'Measured facts' section below lists "
    "table/query names with row counts that were already verified by directly "
    "querying the database — this is a confirmed result, not a hypothesis. Never "
    "invent, guess, or substitute any name not explicitly listed there or in the "
    "root cause text above it."
    "\n\n"
    "At least one entry in Measured facts already has rows greater than 0 — data "
    "exists there. Do NOT say that entry has no data or recommend loading data "
    "into it — that would be factually wrong. If a *different* entry in Measured "
    "facts genuinely shows 0 rows, you may separately state that one has no data "
    "and recommend loading it — but never say that about an entry whose row "
    "count is greater than 0."
    "\n\n"
    "For the condition that is filtering out all the existing data: briefly "
    "interpret what it means in plain business/AML terms using standard OFSAA/"
    "Mantas column-naming conventions (ORIG = originator, ACCT = account, "
    "FL = flag, TRXN = transaction, CNTRPTY = counterparty) — do not just repeat "
    "the raw SQL syntax, and do not describe a scenario unrelated to the actual "
    "condition and facts given below. State specifically what kind of "
    "transactions/records need to be added or adjusted so at least one row "
    "satisfies the condition as it is written — not just 'review the "
    "condition.' If you are not confident what an abbreviation means, say so "
    "briefly rather than guessing confidently — do not invent a meaning you "
    "are unsure of."
    "\n\n"
    "CRITICAL: the analyst reading this cannot edit the scenario's SQL or "
    "query logic — they can only load/change data, or change a configured "
    "threshold value. Never recommend reviewing, adjusting, relaxing, "
    "changing, or modifying the condition, JOIN, or query itself — that is "
    "not an action available to them. Only recommend loading/adjusting data, "
    "or (if a threshold fact is given) changing that threshold's configured "
    "value."
    "\n\n"
    "If a fact gives a configured threshold's current value and description, "
    "explain briefly what it controls using that description, and recommend "
    "lowering the threshold's configured value."
    + _FORMAT_SUFFIX
)

# Used when there's no row-count fact, but a safe, real name exists (a missing
# table/view, or an upstream CTE) — see "Known confirmed details" in the prompt.
_SYSTEM_PROMPT_NAMED = (
    "You are an assistant to an OFSAA/Mantas AML scenario analyst who is debugging "
    "why a scenario produced zero alerts. A 'Known confirmed details' section below "
    "names specific objects already identified by the diagnosis — this is confirmed, "
    "not a guess. Never invent or substitute any name not explicitly listed there or "
    "in the root cause text above it."
    "\n\n"
    "The analyst reading this cannot edit the scenario's SQL or schema "
    "themselves — they can only load/change data, or change a configured "
    "threshold value. If a detail describes a missing table or view, say it "
    "needs to be created/deployed by someone who manages the OFSAA schema — "
    "this needs escalating, do not say 'load data into' it, since it does not "
    "exist yet. If a detail describes an upstream CTE, explain that this CTE "
    "is empty because the named upstream CTE is empty, and recommend "
    "resolving whatever data or threshold issue is causing that upstream CTE "
    "to be empty — never say 'loading data' into a CTE itself (a CTE is a "
    "query subcomponent, not a physical table) and never say to fix/edit the "
    "upstream CTE's SQL."
    + _FORMAT_SUFFIX
)

# Used when neither a measured fact nor a safe named detail exists — genuinely
# nothing concrete to ground on (e.g. parse failures, unclassified JOIN/subquery
# issues). No example sentence with placeholder letters, and an explicit ban on
# inventing table names, since this is exactly the case that leaked "Tables X
# and Y" style text in an earlier version of this prompt.
_SYSTEM_PROMPT_NO_FACTS = (
    "You are an assistant to an OFSAA/Mantas AML scenario analyst who is debugging "
    "why a scenario produced zero alerts. No specific table or row-count facts were "
    "confirmed for this diagnosis — do not name, invent, or guess any table, view, "
    "or column name that does not already appear verbatim in the root cause text "
    "below. Do not claim any table has 0 rows or any specific amount of data, since "
    "that was never measured."
    "\n\n"
    "The analyst reading this cannot edit the scenario's SQL themselves — they "
    "can only load/change data, or change a configured threshold value. Since "
    "nothing concrete was found, this most likely needs the JOIN keys, "
    "subquery logic, or WHERE/HAVING conditions reviewed by someone who can "
    "modify the SQL — say that plainly (e.g. 'escalate this for a developer "
    "to review the JOIN/subquery logic'), rather than telling the analyst to "
    "review or adjust the SQL themselves."
    + _FORMAT_SUFFIX
)

# Used ONLY for the data-insufficiency column-value case (see
# _build_data_value_hint). Asks for exactly ONE line — a plain-English
# explanation of what the named column represents — and explicitly does not
# ask the model to say which value is needed or missing. That second line is
# appended afterward, computed deterministically in Python. Confirmed live,
# across three different fact phrasings (raw values, "DOES/NONE satisfy",
# "ACCEPTED/REJECTED"), that this local 8B model cannot reliably get
# equality/inequality polarity right — it stated the wrong value as "needed"
# on the large majority of runs regardless of wording, while it reliably gets
# the column's plain-English business meaning right every time. Splitting the
# task this way keeps the one thing the model does well and drops the one
# thing it doesn't.
_SYSTEM_PROMPT_COLUMN_MEANING = (
    "You are an assistant to an OFSAA/Mantas AML scenario analyst. A specific "
    "column name is given below, using standard OFSAA/Mantas naming "
    "conventions (ORIG = originator, ACCT = account, FL = flag, TRXN = "
    "transaction, CNTRPTY = counterparty, DT = date). In EXACTLY ONE short "
    "line of plain text, explain what this column represents in plain "
    "business/AML terms — do not just repeat the raw name. If you are not "
    "confident what it means, say so briefly rather than guessing "
    "confidently. Do NOT mention specific values, do NOT say what data is "
    "needed or missing, and do NOT recommend any action — a separate, "
    "already-correct line covering that will be added after your answer. "
    "No headings, no markdown, no preamble."
)

# Defense-in-depth: catches leaked instructional text even if a future prompt
# edit reintroduces the failure mode above, AND catches degenerate repetition
# loops (confirmed live: the model once hallucinated a runaway chain of
# near-duplicate table names — "CUSTOMER_ACCT_MAP", "..._CAM", "..._CAM_ACCT",
# "..._CAM_ACCT_ACCT", etc. — until it hit the token limit mid-word). Neither
# failure mode is prompt-specific, so this check runs on every response
# regardless of which system prompt was used.
_LEAK_MARKERS = ("measured facts section", "tables x and y", "table x and y")
_PLACEHOLDER_TABLE_RE = re.compile(
    r"\btables?\s+['\"]?[A-Z]['\"]?\s+and\s+['\"]?[A-Z]['\"]?\b", re.IGNORECASE
)
# The analysts using this tool cannot edit scenario SQL — only load/change
# data or a configured threshold value. Every system prompt above already
# instructs the model not to suggest editing the condition/query, but
# confirmed live that it can still slip one in unprompted (e.g. "review/
# adjust the JOIN condition"). This is a second, independent line of defense
# that runs regardless of which prompt produced the text. "review" is
# deliberately excluded from the trigger verbs — it's ambiguous (the
# NO_FACTS prompt correctly recommends escalating to "a developer to review
# the JOIN logic", which must NOT be flagged) — only unambiguous self-edit
# verbs are checked.
_CODE_CHANGE_RE = re.compile(
    r"\b(?:adjust|relax|change|modify|update|edit|rewrite|omit|remove|alter)\w*\s+"
    r"(?:the\s+|that\s+|this\s+)?"
    r"(?:condition|query|sql|join|filter|where\s+clause|having\s+clause)\b",
    re.IGNORECASE,
)
_MAX_LINE_CHARS = 400  # loose backstop only — _has_repetition_loop is the real
# defense against runaway output; this just catches the truly extreme case.
# Richer grounded facts (observed column values, threshold descriptions) make
# legitimate 2-sentence answers longer, and the model doesn't always insert a
# newline between them — confirmed live that a correct, well-grounded 340-char
# single-line answer was wrongly rejected at the old 240 cap.


def _has_repetition_loop(text: str) -> bool:
    """Detects the comma/list-style runaway repetition seen live: each item is
    a growing variant of the previous one, so exact n-gram matching won't catch
    it — instead flag when several consecutive comma-separated items share a
    long common prefix (one is literally built by extending the last)."""
    items = [i.strip() for i in text.split(",") if i.strip()]
    if len(items) < 4:
        return False
    run = 1
    for prev, cur in zip(items, items[1:]):
        # Compare the last "word" of each item (handles a leading "and X: ..." prefix)
        prev_tail, cur_tail = prev.split()[-1], cur.split()[-1]
        if len(prev_tail) >= 8 and (cur_tail.startswith(prev_tail) or prev_tail.startswith(cur_tail)):
            run += 1
            if run >= 4:
                return True
        else:
            run = 1
    return False


def _has_leakage(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _LEAK_MARKERS):
        return True
    if _PLACEHOLDER_TABLE_RE.search(text):
        return True
    if any(len(line) > _MAX_LINE_CHARS for line in text.splitlines()):
        return True
    if _CODE_CHANGE_RE.search(text):
        return True
    return _has_repetition_loop(text)


def _extract_measured_facts(results: list[dict]) -> tuple[list[str], bool]:
    """Real row counts already measured by direct queries against Oracle during
    diagnosis — across ALL diagnosed CTEs, not just the first, so a job with
    multiple empty source tables doesn't silently drop everything after
    results[0]. Returns (facts, any_nonzero) — any_nonzero flags whether at
    least one measured count is > 0, so the caller can pick a prompt that
    doesn't ask the model to reason about that branch itself (see
    _SYSTEM_PROMPT_MEASURED_NONZERO)."""
    facts: list[str] = []
    any_nonzero = False

    def add(fact: str, count) -> None:
        nonlocal any_nonzero
        if fact not in facts:
            facts.append(fact)
        if isinstance(count, (int, float)) and count > 0:
            any_nonzero = True

    for r in results:
        source_check = r.get("source_check") or {}
        if source_check.get("table") is not None:
            count = source_check.get("matching_rows", "?")
            add(f"- {source_check['table']}: {count} matching rows", count)

        # NOTE: source_check["observed_values"] (data-insufficiency column
        # probe) is deliberately NOT turned into a fact here. Confirmed live,
        # across three different phrasings, that a local 8B model cannot
        # reliably state which value satisfies an equality/inequality
        # condition even when handed the pre-computed answer as explicit
        # text — it gets the polarity backwards on most runs regardless of
        # wording. That fact is instead handled by a dedicated code path in
        # generate_recommendation() that never asks the model to state the
        # value at all (see _build_data_value_hint) — only to explain the
        # column's business meaning, which it does reliably.

        data_avail = r.get("data_availability") or {}

        for branch in data_avail.get("union_all_branch_counts") or []:
            count = branch.get("row_count", "?")
            add(f"- {branch.get('from_table', '?')}: {count} rows", count)

        if data_avail.get("rows_without_outer_where") is not None:
            count = data_avail["rows_without_outer_where"]
            add(f"- {r.get('cte_name', '?')}: {count} rows before outer WHERE applied", count)

        # JOIN diagnosis: real table + row count, under different key names.
        if data_avail.get("joined_table") and data_avail.get("total_rows") is not None:
            count = data_avail["total_rows"]
            add(f"- {data_avail['joined_table']}: {count} total rows", count)

        # THRESHOLD_KILL diagnosis: inner query row count + the specific
        # threshold conditions that eliminated everything.
        if data_avail.get("inner_query_name") and data_avail.get("inner_query_rows") is not None:
            count = data_avail["inner_query_rows"]
            add(f"- {data_avail['inner_query_name']}: {count} rows available", count)
            for k in data_avail.get("threshold_killers") or []:
                col, op, thresh, actual_max = k.get("column"), k.get("operator"), k.get("threshold"), k.get("actual_max")
                if col:
                    add(f"- threshold condition: {col} {op} {thresh} (actual max={actual_max})", None)

        # Configured threshold values from the scenario's KDD_TSHLD threshold
        # set, matched to the killer column — includes the threshold's real
        # plain-English description when available, so the AI can explain
        # what the threshold means instead of just repeating its number.
        for tshld_name, entry in (data_avail.get("threshold_config") or {}).items():
            desc = entry.get("desc")
            display = entry.get("display_name") or tshld_name
            unit = entry.get("unit") or ""
            fact = f"- configured threshold '{display}': current value {entry.get('curr')}{unit}"
            if entry.get("min") is not None or entry.get("max") is not None:
                fact += f" (allowed range {entry.get('min')}-{entry.get('max')})"
            if desc:
                fact += f" — {desc}"
            add(fact, None)

    return facts, any_nonzero


def _extract_named_entity_notes(results: list[dict]) -> list[str]:
    """Real, safe names the diagnosis already identified, but with no row count
    to go with them — a missing table/view, or an upstream CTE."""
    notes = []
    for r in results:
        missing = r.get("missing_views") or []
        if missing:
            note = f"- Missing table(s)/view(s) that do not exist in the schema: {', '.join(missing)}"
            if note not in notes:
                notes.append(note)

        upstream = r.get("upstream_cte")
        if upstream:
            note = f"- CTE '{r.get('cte_name', '?')}' is empty solely because upstream CTE '{upstream}' is empty (a query subcomponent, not a table)"
            if note not in notes:
                notes.append(note)

    return notes


def _find_all_observed_value_checks(results: list[dict]) -> list[tuple[list[str], dict]]:
    """Returns [(cte_names, source_check), ...] for EVERY distinct data-
    insufficiency column-value probe found (see sql_diagnostics.py's
    _verify_killer_source) across ALL diagnosed CTEs — not just the first, so
    a job with multiple failing CTEs (confirmed live: a real job had 3) gets a
    recommendation covering all of them. Grouped by (table, column, expected
    value): structurally-symmetric CTEs (e.g. 'incoming'/'outgoing' pairs built
    from the same pattern with different aliases) commonly hit the exact same
    underlying condition — cte_names lists every CTE sharing that cause, so
    the recommendation says it applies to both rather than naming only one."""
    order: list[tuple] = []
    grouped: dict[tuple, list[str]] = {}
    fact_by_key: dict[tuple, dict] = {}
    for r in results or []:
        source_check = r.get("source_check") or {}
        if source_check.get("observed_values") and source_check.get("col_name"):
            key = (source_check.get("table"), source_check.get("col_name"), str(source_check.get("expected_value")))
            if key not in grouped:
                grouped[key] = []
                fact_by_key[key] = source_check
                order.append(key)
            grouped[key].append(r.get("cte_name", "?"))
    return [(grouped[key], fact_by_key[key]) for key in order]


def _build_data_value_hint(source_check: dict) -> str:
    """Deterministically states which column value would satisfy the failing
    condition — computed entirely from satisfying_values (itself computed in
    sql_diagnostics.py, not the LLM). Never delegated to the model — see the
    _SYSTEM_PROMPT_COLUMN_MEANING comment for why.

    Data/threshold-only phrasing: the analysts using this tool cannot edit
    scenario SQL — they can only load/adjust data or change a configured
    threshold value. Never offer "relax/change the condition" as an
    alternative, even when no value in the table currently satisfies it —
    that's a code edit, not an option available to them."""
    table = source_check["table"]
    col = source_check["col_name"]
    satisfying = source_check.get("satisfying_values") or []
    if satisfying:
        value = satisfying[0].get("value")
        count = satisfying[0].get("count")
        return (
            f"Load more {table} records with {col} = {value!r} — {count} such "
            f"rows already exist in the table, just not enough to survive the "
            f"rest of the query. More matching records are needed."
        )
    return (
        f"No {table} records currently have a {col} value that satisfies the "
        f"condition — load data into {table} with a {col} value that does."
    )


def _build_prompt(root_cause: str, results: list[dict] | None) -> tuple[str, str]:
    """Returns (prompt_text, system_prompt) — the system prompt is selected
    based on what kind of grounding is actually available, so a model never
    sees instructions/examples about facts it wasn't given."""
    results = results or []
    parts = [f"Root cause diagnosis:\n{(root_cause or '').strip()[:1500]}"]

    top = results[0] if results else None
    if top:
        if top.get("failure_type"):
            parts.append(f"Failure type: {top['failure_type']}")
        if top.get("failure_condition"):
            parts.append(f"Failing condition: {str(top['failure_condition'])[:400]}")
        if top.get("likely_cause"):
            parts.append(f"Likely cause: {str(top['likely_cause'])[:400]}")

    capped_results = results[:8]
    measured_facts, any_nonzero = _extract_measured_facts(capped_results)
    named_notes = _extract_named_entity_notes(capped_results)

    if measured_facts:
        parts.append("Measured facts (verified by direct query, not a guess):\n" + "\n".join(measured_facts))
        system_prompt = _SYSTEM_PROMPT_MEASURED_NONZERO if any_nonzero else _SYSTEM_PROMPT_MEASURED
    elif named_notes:
        parts.append("Known confirmed details (verified by the diagnosis, not a guess):\n" + "\n".join(named_notes))
        system_prompt = _SYSTEM_PROMPT_NAMED
    else:
        system_prompt = _SYSTEM_PROMPT_NO_FACTS

    return "\n".join(parts), system_prompt


def _clean_to_two_lines(text: str) -> str:
    lines = [ln.strip(" \t-*•") for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[:2])


def _call_ollama_once(prompt: str, system_prompt: str, temperature: float) -> str | None:
    """Single Ollama call. Returns the raw stripped response text, or None on
    any transport/timeout/empty-response failure (logged, never raised)."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "system": system_prompt,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 120},
        # Keep the model resident in memory between jobs so most calls hit a
        # warm model (~5-10s) instead of paying a ~40s cold-load penalty
        # every time Ollama's default 5-minute idle-unload kicks in.
        "keep_alive": "30m",
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = (body.get("response") or "").strip()
        if not text:
            logger.warning("AI recommendation: empty response from Ollama")
            return None
        return text
    except urllib.error.URLError as e:
        logger.warning("AI recommendation skipped - Ollama unreachable at %s: %s", OLLAMA_BASE_URL, e)
        return None
    except TimeoutError:
        logger.warning("AI recommendation skipped - Ollama timed out after %ss", OLLAMA_TIMEOUT_S)
        return None
    except Exception as e:
        logger.warning("AI recommendation skipped - unexpected error: %s", e)
        return None


def generate_recommendation(root_cause: str, results: list[dict] | None = None) -> str | None:
    """Return a recommendation string (normally 2 lines; more when multiple
    distinct data-insufficiency causes were diagnosed — see
    _generate_data_value_recommendation), or None if unavailable/disabled.

    Retries once (same prompt, slightly higher temperature) if the first
    response fails the leakage/repetition guard — confirmed live that this
    local 8B model occasionally produces a degenerate repetition loop or
    echoes prompt text, and a second sample from the same prompt commonly
    succeeds. Never retries on a transport/timeout failure (Ollama being
    genuinely unreachable won't fix itself in a few seconds) — only on a
    successful-but-invalid response.

    Data-insufficiency column-value case (see _build_data_value_hint) is
    handled specially: the "which value is needed" line is always computed
    deterministically and never left to the model, since that specific
    reasoning step was confirmed unreliable across repeated live testing."""
    if not AI_RECOMMENDATION_ENABLED or not root_cause:
        return None

    observed_checks = _find_all_observed_value_checks(results or [])
    if observed_checks:
        return _generate_data_value_recommendation(root_cause, observed_checks)

    prompt, system_prompt = _build_prompt(root_cause, results)

    for attempt, temperature in enumerate((0.2, 0.4)):
        text = _call_ollama_once(prompt, system_prompt, temperature)
        if text is None:
            return None  # transport/timeout failure — retrying won't help
        cleaned = _clean_to_two_lines(text)
        if not _has_leakage(cleaned):
            return cleaned
        logger.warning(
            "AI recommendation attempt %d/2 rejected (leaked/degenerate text): %r",
            attempt + 1, cleaned,
        )

    return None


def _generate_data_value_recommendation(root_cause: str, checks: list[tuple[list[str], dict]]) -> str:
    """The data-insufficiency hybrid path: for EACH distinct diagnosed cause
    (there can be several — confirmed live, a single job had 3 failing CTEs
    with 3 different reasons), asks the model for one line explaining the
    column's business meaning (which it does reliably), and always appends a
    deterministically-computed line stating which value would satisfy that
    CTE's condition (which the model does not do reliably — see
    generate_recommendation's docstring). Always returns a real
    recommendation, never None — the deterministic lines alone are already
    correct and useful even if every LLM call fails.

    Deliberately not capped at 2 lines when there's more than one distinct
    cause: the point of this path is covering every diagnosed failure, not
    brevity — this also flows straight into the PDF report, which renders
    ai_recommendation verbatim (see pdf_service.py's _root_cause_section)."""
    multi = len(checks) > 1
    blocks = []
    for cte_names, source_check in checks:
        hint = _build_data_value_hint(source_check)

        prompt = (
            f"Column: {source_check['table']}.{source_check['col_name']}\n"
            f"Context: {(root_cause or '').strip()[:300]}"
        )

        meaning_line = ""
        for temperature in (0.2, 0.4):
            text = _call_ollama_once(prompt, _SYSTEM_PROMPT_COLUMN_MEANING, temperature)
            if text is None:
                break  # transport/timeout failure — fall through to hint-only
            candidate = text.strip().splitlines()[0].strip(" \t-*•") if text.strip() else ""
            if candidate and not _has_leakage(candidate):
                meaning_line = candidate
                break

        if multi:
            names = " and ".join(f"'{n}'" for n in cte_names)
            prefix = f"CTE {names}: "
        else:
            prefix = ""
        block = f"{prefix}{meaning_line}\n{hint}" if meaning_line else f"{prefix}{hint}"
        blocks.append(block)

    return "\n\n".join(blocks)
