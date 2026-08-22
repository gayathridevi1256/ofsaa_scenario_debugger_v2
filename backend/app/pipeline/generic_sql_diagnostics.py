"""
generic_sql_diagnostics.py

A scenario-agnostic SQL diagnostic engine for OFSAA function scenarios.
No knowledge of any particular business logic is baked in. It only
understands SQL structure:

  1. STRUCTURAL DECOMPOSITION
     Recursively break any query into its natural pieces:
       - WITH ... AS (...) CTEs + the final SELECT that follows
       - top-level UNION / UNION ALL branches
       - a single top-level FROM (subquery) derived table
     ...stopping at "leaf" queries that have none of the above.

  2. AUTO-LOCALIZATION
     Given a working row-count executor, walk the tree top-down: if a
     node returns 0 rows, decide whether the zero is caused by its own
     filtering (WHERE / JOIN...ON) or by something deeper (a child that's
     also empty), and recurse accordingly.

  3. CONDITION ELIMINATION
     Once localized to the offending node, split its WHERE clause (and/or
     its JOIN...ON clauses) into individual AND-connected conditions and
     test removing/adding them one at a time to isolate the blocker(s).
"""
import re
import os
import logging
import sqlparse
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_DATE_PARAM_WORDS = ("DATE", "BUSINESS_DATE", "BATCH_DATE", "CURRENT_DATE")


# ---------------------------------------------------------------------------
# Comment-safe primitives
# ---------------------------------------------------------------------------

def strip_comments(sql: str) -> str:
    """Strip SQL comments. Uses regex for large queries to avoid sqlparse token limits."""
    if len(sql) > 50000:
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        sql = re.sub(r'--[^\n]*', '', sql)
        return sql.strip()
    try:
        return sqlparse.format(sql, strip_comments=True).strip('\n')
    except Exception:
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        sql = re.sub(r'--[^\n]*', '', sql)
        return sql.strip()


def matching_close_paren(sql: str, open_idx: int) -> Optional[int]:
    depth = 0
    for i in range(open_idx, len(sql)):
        if sql[i] == '(':
            depth += 1
        elif sql[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return None


def top_level_finditer(sql: str, pattern: re.Pattern):
    """Yield match objects for `pattern`, only where paren-depth == 0."""
    depth = 0
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == '(':
            depth += 1
            i += 1
            continue
        if ch == ')':
            depth -= 1
            i += 1
            continue
        if depth == 0:
            m = pattern.match(sql, i)
            if m:
                yield m
                i = m.end()
                continue
        i += 1


# ---------------------------------------------------------------------------
# Generic AND-condition splitting, reusable for WHERE or ON clause bodies
# ---------------------------------------------------------------------------

def split_and_conditions(body: str) -> List[str]:
    """Split a clause body into top-level (paren-depth 0) AND-connected parts."""
    conditions = []
    depth = 0
    last_cut = 0
    i = 0
    pattern = re.compile(r'\band\b', flags=re.IGNORECASE)
    while i < len(body):
        ch = body[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0:
            m = pattern.match(body, i)
            if m:
                conditions.append(body[last_cut:i].strip())
                i = m.end()
                last_cut = i
                continue
        i += 1
    conditions.append(body[last_cut:].strip())
    return [c for c in conditions if c]


_CLAUSE_BOUNDARY = re.compile(
    r'\b(where|group\s+by|order\s+by|having|connect\s+by|start\s+with|'
    r'inner\s+join|left\s+(outer\s+)?join|right\s+(outer\s+)?join|full\s+(outer\s+)?join|join|'
    r'union\s+all|union)\b',
    flags=re.IGNORECASE,
)


def _find_clause_end(sql: str, body_start: int) -> int:
    """Find where a clause body ends: next top-level boundary keyword,
    an unmatched ')', or end of string."""
    depth = 0
    j = body_start
    while j < len(sql):
        ch = sql[j]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return j
        elif depth == 0:
            m = _CLAUSE_BOUNDARY.match(sql[j:])
            if m:
                return j
        j += 1
    return len(sql)


def find_where_clause(sql: str) -> Optional[Tuple[int, int]]:
    """Return (body_start, body_end) of the first top-level WHERE, or None."""
    pattern = re.compile(r'\bwhere\b', flags=re.IGNORECASE)
    for m in top_level_finditer(sql, pattern):
        body_start = m.end()
        body_end = _find_clause_end(sql, body_start)
        return body_start, body_end
    return None


def find_on_clauses(sql: str) -> List[Tuple[int, int]]:
    """Return [(body_start, body_end), ...] for every top-level JOIN...ON."""
    pattern = re.compile(r'\bon\b', flags=re.IGNORECASE)
    spans = []
    for m in top_level_finditer(sql, pattern):
        body_start = m.end()
        body_end = _find_clause_end(sql, body_start)
        spans.append((body_start, body_end))
    return spans


def splice(sql: str, span: Tuple[int, int], replacement: str) -> str:
    start, end = span
    return sql[:start] + ' ' + replacement + ' ' + sql[end:]


# ---------------------------------------------------------------------------
# Executor interface
# ---------------------------------------------------------------------------

@dataclass
class Executor:
    run_count_fn: Callable[[str], int]

    def run_count(self, sql: str) -> int:
        return self.run_count_fn(sql)


class OracleExecutor(Executor):
    """Wraps an Oracle connection for the generic engine."""

    def __init__(self, conn, metadata: dict = None, output_dir: str = None):
        self.conn = conn
        self.metadata = metadata or {}
        self.output_dir = output_dir or ""
        self._threshold_bindings = None
        super().__init__(run_count_fn=self._run_count)

    def _run_count(self, sql: str) -> int:
        processed = self._resolve_params(sql)
        if not processed or not processed.strip():
            return 0
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM (\n{processed}\n) t")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.debug("OracleExecutor run_count error: %s", e)
            return 0
        finally:
            cursor.close()

    def _resolve_params(self, sql: str) -> str:
        safe = sql
        bd = self.metadata.get("current_business_date")
        date_replacement = f"TO_DATE('{bd}','YYYY-MM-DD')" if bd else "SYSDATE"

        def _replace_var(m):
            name = m.group(1)
            if any(d in name.upper() for d in _DATE_PARAM_WORDS):
                return date_replacement
            bindings = self._get_threshold_bindings()
            if bindings and name in bindings:
                try:
                    v = float(bindings[name])
                    return str(int(v)) if v == int(v) else str(v)
                except (ValueError, TypeError):
                    pass
            return date_replacement

        safe = re.sub(r'@([\w_]+)', _replace_var, safe)
        return re.sub(r'/\*.*?\*/', '', safe, flags=re.DOTALL).strip()

    def _get_threshold_bindings(self) -> dict:
        if self._threshold_bindings is not None:
            return self._threshold_bindings
        tshld_set_id = self.metadata.get("tshld_set_id")
        if not tshld_set_id:
            self._threshold_bindings = {}
            return self._threshold_bindings
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT BINDING_NM, BINDING_VALUE_TX
                FROM   fccmatomic.KDD_TSHLD_BINDING
                WHERE  TSHLD_SET_ID = :1
            """, [str(tshld_set_id)])
            self._threshold_bindings = {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.debug("Could not fetch threshold bindings: %s", e)
            self._threshold_bindings = {}
        finally:
            cursor.close()
        return self._threshold_bindings


# ---------------------------------------------------------------------------
# Structural tree
# ---------------------------------------------------------------------------

@dataclass
class Node:
    label: str
    kind: str
    runnable_sql: str
    children: List['Node'] = field(default_factory=list)


_WITH_CTE_HEAD = re.compile(r'\bwith\s+(\w+)\s+as\s*\(', flags=re.IGNORECASE)


def _parse_with_ctes(sql: str):
    """Parse `WITH a AS (...), b AS (...) final_select`.
    Returns ([(name, body), ...], final_select) or (None, None) if no WITH."""
    m = _WITH_CTE_HEAD.match(sql.lstrip())
    if not m:
        return None, None

    ctes = []
    pos = m.start() + m.group().rfind('(')
    while True:
        close = matching_close_paren(sql, pos)
        if close is None:
            return None, None
        before = sql[m.start():pos].strip()
        name_match = re.search(r'(\w+)\s+as\s*$', before, flags=re.IGNORECASE)
        cte_name = name_match.group(1) if name_match else f'cte{len(ctes)+1}'
        ctes.append((cte_name, sql[pos + 1:close].strip()))

        rest = sql[close + 1:].lstrip()
        if rest.startswith(','):
            rest_after_comma = rest[1:].lstrip()
            m2 = re.match(r'(\w+)\s+as\s*\(', rest_after_comma, flags=re.IGNORECASE)
            if not m2:
                break
            offset = len(sql) - len(rest_after_comma)
            pos = offset + m2.end() - 1
            continue
        else:
            final_select = rest.rstrip().rstrip(';').strip()
            return ctes, final_select

    return None, None


def _find_top_level_from_subquery(sql: str) -> Optional[Tuple[int, int]]:
    """Index span (open_idx, close_idx) of the '(' ... ')' immediately
    following the first top-level FROM, if the FROM's source is a
    parenthesized subquery (a derived table)."""
    pattern = re.compile(r'\bfrom\b', flags=re.IGNORECASE)
    for m in top_level_finditer(sql, pattern):
        i = m.end()
        while i < len(sql) and sql[i].isspace():
            i += 1
        if i < len(sql) and sql[i] == '(':
            close = matching_close_paren(sql, i)
            if close is not None:
                return i, close
        return None
    return None


def build_tree(sql: str, label: str = 'root', kind: str = 'ROOT', cte_prefix: str = '') -> Node:
    clean = strip_comments(sql).strip()
    runnable = f'{cte_prefix} {clean}' if cte_prefix else clean
    node = Node(label=label, kind=kind, runnable_sql=runnable)

    ctes, final_select = _parse_with_ctes(clean)
    if ctes:
        cte_defs_sql = ', '.join(f'{name} as ({body})' for name, body in ctes)
        for name, body in ctes:
            child = build_tree(body, label=f'{label} > CTE[{name}]', kind='CTE')
            node.children.append(child)
        final_prefix = f'with {cte_defs_sql}'
        final_node = build_tree(final_select, label=f'{label} > FINAL', kind='FINAL',
                                 cte_prefix=final_prefix)
        node.children.append(final_node)
        return node

    branches = _split_top_level_union(clean)
    if len(branches) > 1:
        for i, branch_sql in enumerate(branches, start=1):
            child = build_tree(branch_sql, label=f'{label} > BRANCH[{i}]', kind='UNION_BRANCH',
                                cte_prefix=cte_prefix)
            node.children.append(child)
        return node

    span = _find_top_level_from_subquery(clean)
    if span:
        open_idx, close_idx = span
        inner_sql = clean[open_idx + 1:close_idx].strip()
        child = build_tree(inner_sql, label=f'{label} > DERIVED', kind='DERIVED_TABLE',
                            cte_prefix=cte_prefix)
        node.children.append(child)
        return node

    if kind == 'ROOT':
        node.kind = 'LEAF'
    return node


def _split_top_level_union(sql: str) -> List[str]:
    pattern = re.compile(r'\bunion\s+all\b|\bunion\b', flags=re.IGNORECASE)
    depth = 0
    segments = []
    last_cut = 0
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0:
            m = pattern.match(sql, i)
            if m:
                segments.append(sql[last_cut:i].strip())
                i = m.end()
                last_cut = i
                continue
        i += 1
    segments.append(sql[last_cut:].strip())
    return [s for s in segments if s]


# ---------------------------------------------------------------------------
# Condition elimination (generic -- works on WHERE and JOIN...ON alike)
# ---------------------------------------------------------------------------

def eliminate_where_conditions(sql: str, executor: Executor) -> Dict:
    span = find_where_clause(sql)
    if span is None:
        return {'found': False}
    body_start, body_end = span
    body = sql[body_start:body_end]
    conditions = split_and_conditions(body)

    baseline = executor.run_count(sql)
    results = []
    for i, cond in enumerate(conditions):
        remaining = conditions[:i] + conditions[i + 1:]
        new_body = ' AND '.join(remaining) if remaining else '1=1'
        variant = sql[:body_start] + ' ' + new_body + ' ' + sql[body_end:]
        cnt = executor.run_count(variant)
        results.append({
            'condition': cond,
            'row_count_without_this': cnt,
            'likely_blocker': baseline == 0 and cnt > 0,
        })
    return {'found': True, 'clause': 'WHERE', 'baseline': baseline, 'conditions': results}


def eliminate_on_conditions(sql: str, executor: Executor) -> Dict:
    spans = find_on_clauses(sql)
    if not spans:
        return {'found': False}

    baseline = executor.run_count(sql)
    all_results = []
    for join_idx, (body_start, body_end) in enumerate(spans, start=1):
        body = sql[body_start:body_end]
        conditions = split_and_conditions(body)
        for cond_idx, cond in enumerate(conditions):
            remaining = [c for c in conditions if c != cond]
            new_body = ' AND '.join(remaining) if remaining else '1=1'
            variant = sql[:body_start] + ' ' + new_body + ' ' + sql[body_end:]
            cnt = executor.run_count(variant)
            all_results.append({
                'join_number': join_idx,
                'condition': cond,
                'row_count_without_this': cnt,
                'likely_blocker': baseline == 0 and cnt > 0,
            })
    return {'found': True, 'clause': 'ON', 'baseline': baseline, 'conditions': all_results}


# ---------------------------------------------------------------------------
# Auto-localization: walk the tree from the empty root down to the blocker
# ---------------------------------------------------------------------------

@dataclass
class LocalizationStep:
    label: str
    kind: str
    row_count: int


def _likely_cause_from_condition(condition: str) -> str:
    c = condition.upper()
    if "SELECT" in c:
        if any(k in c for k in ["MIN_DT", "MAX_DT", "DATE", "DT"]):
            return "Date filter too restrictive -- no rows fall in the date range returned by the subquery"
        return "Subquery returns no rows or NULL -- condition filters everything out"
    if " IN " in c and "(" in c:
        return "IN list has no matching values in the data"
    if any(op in condition for op in [">=", "<=", ">", "<"]):
        if any(d in c for d in ["_DT", "DATE", "TRUNC", "SYSDATE"]):
            return "Date comparison too restrictive -- data does not fall in the expected range"
        return "Thresholds are set too high -- range condition eliminates all rows"
    if "=" in c and "NULL" not in c:
        return "Equality filter has no matching values in the data"
    if "IS NULL" in c or "IS NOT NULL" in c:
        return "NULL check filtering out all rows -- unexpected NULLs or non-NULLs in data"
    if "COALESCE" in c or "NVL" in c:
        return "COALESCE/NVL condition filtering out all rows -- check default value logic"
    if "LIKE" in c:
        return "LIKE pattern has no matching values in the data"
    return "Condition is too restrictive -- no rows satisfy this filter"


def _find_condition_line_in_sql(condition: str, sql_text: str) -> Optional[int]:
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
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\s*([<>=!]+)\s*', r' \1 ', text)
        text = text.upper()
        text = re.sub(r'\bNOT\s+(.*?)\s+IS\s+NULL\b', r'\1 IS NOT NULL', text)
        return text

    def _try_match(search_text: str) -> Optional[int]:
        search = _normalize(search_text)
        for lineno, line in enumerate(sql_text.splitlines(), start=1):
            line_norm = _normalize(line)
            if search in line_norm:
                return lineno

        search_short = search[:200]
        s_lines = sql_text.splitlines()
        for window in range(2, 5):
            for i in range(len(s_lines) - window + 1):
                block = ' '.join(s_lines[i:i + window])
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
                for i in range(len(s_lines) - window + 1):
                    block = ' '.join(s_lines[i:i + window])
                    if search_phrase in _normalize(block):
                        return i + 1
        return None

    result = _try_match(condition)
    if result is not None:
        return result + header_offset

    from app.pipeline.sql_diagnostics import _split_and_conditions
    parts = _split_and_conditions(condition)
    if len(parts) > 1:
        for part in parts:
            result = _try_match(part.strip())
            if result is not None:
                return result + header_offset

    return None


def localize_and_diagnose(root_sql: str, executor: Executor) -> Dict:
    tree = build_tree(root_sql)
    path: List[LocalizationStep] = []

    node = tree
    while True:
        count = executor.run_count(node.runnable_sql)
        path.append(LocalizationStep(node.label, node.kind, count))

        if count > 0 and node is tree:
            return {'status': 'OK', 'row_count': count, 'path': path}

        if not node.children:
            where_result = eliminate_where_conditions(node.runnable_sql, executor)
            if where_result.get('found'):
                return {'status': 'LOCALIZED', 'node': node.label, 'path': path,
                        'elimination': where_result}
            on_result = eliminate_on_conditions(node.runnable_sql, executor)
            if on_result.get('found'):
                return {'status': 'LOCALIZED', 'node': node.label, 'path': path,
                        'elimination': on_result}
            return {'status': 'NO_FILTER_FOUND', 'node': node.label, 'path': path,
                    'note': 'This query has no top-level WHERE or JOIN...ON to test. '
                            'Zero rows likely means one of the referenced tables/CTEs '
                            'itself has no matching data -- check that directly.'}

        child_counts = [(child, executor.run_count(child.runnable_sql)) for child in node.children]

        final_children = [c for c, _ in child_counts if c.kind == 'FINAL']
        if final_children:
            node = final_children[0]
            continue

        any_child_has_rows = any(c > 0 for _, c in child_counts)
        if count == 0 and any_child_has_rows and len(node.children) == 1:
            where_result = eliminate_where_conditions(node.runnable_sql, executor)
            if where_result.get('found'):
                return {'status': 'LOCALIZED', 'node': node.label, 'path': path,
                        'elimination': where_result}
            on_result = eliminate_on_conditions(node.runnable_sql, executor)
            if on_result.get('found'):
                return {'status': 'LOCALIZED', 'node': node.label, 'path': path,
                        'elimination': on_result}

        empty_children = [(child, c) for child, c in child_counts if c == 0]
        if empty_children:
            node = empty_children[0][0]
            continue

        return {'status': 'INCONCLUSIVE', 'node': node.label, 'path': path,
                'child_counts': [(c.label, cnt) for c, cnt in child_counts]}


def format_localization_report(result: Dict) -> str:
    lines = []
    W = 70
    lines.append('=' * W)
    lines.append('GENERIC SQL DIAGNOSTIC REPORT'.center(W))
    lines.append('=' * W)
    lines.append('PATH TRAVERSED:')
    for step in result['path']:
        mark = 'OK' if step.row_count > 0 else 'FAIL'
        lines.append(f'  {step.label:45s} {step.row_count:>7} rows  {mark}')
    lines.append('-' * W)

    status = result['status']
    if status == 'OK':
        lines.append(f"Root query returned {result['row_count']} rows -- no issue found.")
    elif status == 'LOCALIZED':
        elim = result['elimination']
        lines.append(f"Localized to: {result['node']}")
        lines.append(f"Clause tested: {elim['clause']}  (baseline row count: {elim['baseline']})")
        lines.append('')
        blockers = [c for c in elim['conditions'] if c['likely_blocker']]
        if blockers:
            lines.append('LIKELY BLOCKING CONDITION(S):')
            for b in blockers:
                lines.append(f"  - {b['condition']}")
                lines.append(f"      (removing it alone -> {b['row_count_without_this']} rows)")
        else:
            lines.append('No single condition alone explains the zero rows.')
            lines.append('Row counts with each condition removed individually:')
            for c in elim['conditions']:
                lines.append(f"  - {c['condition'][:60]:60s} -> {c['row_count_without_this']} rows")
            lines.append('')
            lines.append('Consider progressive_addition-style testing for multi-condition conflicts.')
    elif status == 'NO_FILTER_FOUND':
        lines.append(f"Localized to: {result['node']}")
        lines.append(result['note'])
    else:
        lines.append(f"Could not fully localize past: {result['node']}")
        lines.append('Child row counts:')
        for label, cnt in result.get('child_counts', []):
            lines.append(f'  {label:45s} {cnt:>7} rows')

    lines.append('=' * W)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Result converter: maps generic output to existing diagnostic format
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# JOIN data availability: test if tables have matching rows for JOIN conditions
# ---------------------------------------------------------------------------

def _extract_tables_and_joins(sql: str) -> Optional[Dict]:
    """Extract table aliases, table names, and JOIN conditions from a leaf SQL."""
    sql_upper = sql.upper()
    
    # Find FROM clause
    from_match = re.search(r'\bFROM\b\s+', sql_upper)
    if not from_match:
        return None
    
    from_idx = from_match.start()
    
    # Find WHERE clause to know where FROM ends
    where_match = re.search(r'\bWHERE\b\s+', sql_upper[from_idx:])
    if where_match:
        from_body = sql[from_idx:from_idx + where_match.start()]
        where_body = sql[from_idx + where_match.start():]
    else:
        from_body = sql[from_idx:]
        where_body = ''
    
    # Parse tables and aliases from FROM clause
    # Handle comma-separated tables and implicit joins
    tables = []
    alias_map = {}  # alias -> table_name
    
    # Split by comma for implicit joins
    table_parts = re.split(r',', from_body.strip())
    
    for part in table_parts:
        part = part.strip()
        if not part:
            continue
        # Skip subqueries — only parse real table references
        if '(' in part:
            continue
        # Match table_name [alias] pattern
        tbl_match = re.search(r'([\w.]+)\s+(?:AS\s+)?(\w+)', part, re.IGNORECASE)
        if tbl_match:
            table_name = tbl_match.group(1)
            alias = tbl_match.group(2)
            if alias.upper() not in ('WHERE', 'AND', 'OR', 'ON', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'SELECT', 'FROM'):
                tables.append({'name': table_name, 'alias': alias})
                alias_map[alias.upper()] = table_name
    
    # Extract JOIN conditions from WHERE clause (implicit joins)
    # Only include conditions where BOTH aliases map to real tables (not subqueries)
    join_conditions = []
    if where_body:
        conditions = re.split(r'\bAND\b', where_body, flags=re.IGNORECASE)
        for cond in conditions:
            cond = cond.strip()
            # Look for alias.column = alias.column patterns
            eq_match = re.search(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', cond)
            if eq_match:
                left_alias = eq_match.group(1).upper()
                right_alias = eq_match.group(3).upper()
                # Only include if both aliases are real tables we parsed
                if left_alias in alias_map and right_alias in alias_map:
                    join_conditions.append(cond)
    
    return {
        'tables': tables,
        'alias_map': alias_map,
        'join_conditions': join_conditions,
        'where_body': where_body,
    }


def test_join_data_availability(sql: str, executor: Executor) -> Dict:
    """
    When condition elimination finds no single blocker, test if the JOIN itself
    is the problem by checking if tables have matching data.
    
    Returns a dict with join analysis results.
    """
    parsed = _extract_tables_and_joins(sql)
    if not parsed or len(parsed['tables']) < 2:
        return {'available': True, 'note': 'Cannot parse tables from query'}
    
    tables = parsed['tables']
    join_conds = parsed['join_conditions']
    
    if not join_conds:
        return {'available': True, 'note': 'No JOIN conditions found to test'}
    
    results = []
    
    # Test 1: Check row counts for each table individually (with non-JOIN WHERE filters)
    for tbl in tables:
        try:
            count_sql = f"SELECT COUNT(*) FROM {tbl['name']} {tbl['alias']}"
            total = executor.run_count(count_sql)
            results.append({
                'table': tbl['name'],
                'alias': tbl['alias'],
                'total_rows': total,
            })
        except Exception as e:
            results.append({
                'table': tbl['name'],
                'alias': tbl['alias'],
                'total_rows': -1,
                'error': str(e),
            })
    
    # Test 2: For each JOIN condition, test if the two tables have matching keys
    for join_cond in join_conds:
        eq_match = re.search(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', join_cond)
        if not eq_match:
            continue
        
        left_alias = eq_match.group(1)
        left_col = eq_match.group(2)
        right_alias = eq_match.group(3)
        right_col = eq_match.group(4)
        
        left_table = parsed['alias_map'].get(left_alias.upper(), left_alias)
        right_table = parsed['alias_map'].get(right_alias.upper(), right_alias)
        
        try:
            # Count distinct keys in left table
            left_keys_sql = f"SELECT COUNT(DISTINCT {left_alias}.{left_col}) FROM {left_table} {left_alias}"
            left_keys = executor.run_count(left_keys_sql)
            
            # Count distinct keys in right table
            right_keys_sql = f"SELECT COUNT(DISTINCT {right_alias}.{right_col}) FROM {right_table} {right_alias}"
            right_keys = executor.run_count(right_keys_sql)
            
            # Count matching pairs
            match_sql = f"""
                SELECT COUNT(*)
                FROM {left_table} {left_alias}
                INNER JOIN {right_table} {right_alias}
                    ON {left_alias}.{left_col} = {right_alias}.{right_col}
            """
            matches = executor.run_count(match_sql)
            
            results.append({
                'join_condition': join_cond,
                'left_table': left_table,
                'left_alias': left_alias,
                'left_col': left_col,
                'left_distinct_keys': left_keys,
                'right_table': right_table,
                'right_alias': right_alias,
                'right_col': right_col,
                'right_distinct_keys': right_keys,
                'matching_pairs': matches,
                'join_produces_rows': matches > 0,
            })
        except Exception as e:
            results.append({
                'join_condition': join_cond,
                'error': str(e),
                'join_produces_rows': False,
            })
    
    # Test 3: Find the first JOIN that produces 0 rows
    zero_joins = [r for r in results if isinstance(r.get('matching_pairs'), int) and r['matching_pairs'] == 0]
    
    return {
        'available': len(zero_joins) == 0,
        'tables': [r for r in results if 'total_rows' in r],
        'joins': [r for r in results if 'join_condition' in r],
        'zero_joins': zero_joins,
        'root_cause': zero_joins[0] if zero_joins else None,
    }


def format_join_report(join_result: Dict) -> str:
    """Format join data availability report."""
    lines = []
    W = 70
    lines.append('=' * W)
    lines.append('JOIN DATA AVAILABILITY REPORT'.center(W))
    lines.append('=' * W)
    
    if join_result.get('tables'):
        lines.append('')
        lines.append('TABLE ROW COUNTS:')
        for tbl in join_result['tables']:
            rows = f"{tbl['total_rows']:,}" if tbl['total_rows'] >= 0 else 'ERROR'
            lines.append(f"  {tbl['alias']:8s} ({tbl['table']:30s}): {rows} rows")
    
    if join_result.get('joins'):
        lines.append('')
        lines.append('JOIN ANALYSIS:')
        for j in join_result['joins']:
            if 'error' in j:
                lines.append(f"  {j['join_condition']}: ERROR - {j['error']}")
                continue
            
            status = "OK" if j['join_produces_rows'] else "ZERO ROWS"
            lines.append(f"  {j['join_condition']}")
            lines.append(f"    Left:  {j['left_table']} ({j['left_alias']}) - {j['left_distinct_keys']:,} distinct keys")
            lines.append(f"    Right: {j['right_table']} ({j['right_alias']}) - {j['right_distinct_keys']:,} distinct keys")
            lines.append(f"    Matches: {j['matching_pairs']:,} rows  [{status}]")
    
    if join_result.get('root_cause'):
        rc = join_result['root_cause']
        lines.append('')
        lines.append('ROOT CAUSE:')
        lines.append(f"  The JOIN condition '{rc['join_condition']}' produces 0 rows.")
        lines.append(f"  {rc['left_table']} has {rc['left_distinct_keys']:,} distinct {rc['left_col']} values.")
        lines.append(f"  {rc['right_table']} has {rc['right_distinct_keys']:,} distinct {rc['right_col']} values.")
        lines.append(f"  But NO rows match between them.")
    
    lines.append('=' * W)
    return '\n'.join(lines)


def convert_to_diagnostic_result(result: Dict, original_sql: str, cte_name: str = "resolved_function") -> dict:
    """Maps generic engine output to the existing diagnostic result format."""
    if result["status"] == "OK":
        return {
            "cte_name": cte_name,
            "failure_type": "none",
            "failure_condition": None,
            "rows_before": None,
            "rows_after": result.get("row_count", 0),
            "likely_cause": f"Query returned {result['row_count']:,} rows -- no issue found.",
            "threshold_suggestions": [],
            "data_availability": {},
        }

    if result["status"] == "LOCALIZED":
        elim = result["elimination"]
        blockers = [c for c in elim["conditions"] if c["likely_blocker"]]

        if blockers:
            failure_cond = blockers[0]["condition"]
            rows_after = blockers[0]["row_count_without_this"]
        elif elim["conditions"]:
            failure_cond = elim["conditions"][0]["condition"]
            rows_after = elim["conditions"][0]["row_count_without_this"]
        else:
            failure_cond = None
            rows_after = 0

        likely_cause = _likely_cause_from_condition(failure_cond) if failure_cond else "Unknown blocking condition"

        where_analysis = []
        for c in elim.get("conditions", []):
            where_analysis.append({
                "condition": c["condition"],
                "rows_without_this_cond": c["row_count_without_this"],
                "kills_rows": c["likely_blocker"],
            })

        # Build data_availability with where_analysis for UI display
        data_availability = {}
        if elim["clause"] == "WHERE" and where_analysis:
            data_availability["where_analysis"] = where_analysis

        diag_result = {
            "cte_name": cte_name,
            "failure_type": elim["clause"],
            "failure_condition": failure_cond,
            "rows_before": elim["baseline"],
            "rows_after": rows_after,
            "likely_cause": likely_cause,
            "threshold_suggestions": [],
            "data_availability": data_availability,
            "where_analysis": where_analysis if elim["clause"] == "WHERE" else [],
        }

        if failure_cond:
            line_num = _find_condition_line_in_sql(failure_cond, original_sql)
            if line_num:
                diag_result["condition_line_number"] = line_num

        return diag_result

    if result["status"] == "NO_FILTER_FOUND":
        return {
            "cte_name": cte_name,
            "failure_type": "no_filter",
            "failure_condition": None,
            "rows_before": None,
            "rows_after": 0,
            "likely_cause": result.get("note", "No WHERE or JOIN...ON conditions found to test."),
            "threshold_suggestions": [],
            "data_availability": {},
        }

    child_info = []
    for label, cnt in result.get("child_counts", []):
        child_info.append({"label": label, "row_count": cnt})

    return {
        "cte_name": cte_name,
        "failure_type": "inconclusive",
        "failure_condition": None,
        "rows_before": None,
        "rows_after": 0,
        "likely_cause": f"Could not fully localize past {result.get('node', 'unknown')}. Check child row counts.",
        "threshold_suggestions": [],
        "data_availability": {"child_counts": child_info},
    }
