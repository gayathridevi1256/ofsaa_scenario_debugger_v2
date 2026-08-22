import os
import re
import json
import sqlglot

from sqlglot import expressions as exp

from path_manager import (
    get_latest_output_directory
)

# ----------------------------------------------------------------------
# CTE PARSER
# ----------------------------------------------------------------------


class CTEParser:

    def __init__(self, sql_text: str):

        self.original_sql = sql_text.strip()

        self.parsed = None

        self._parse_sql()

    # ------------------------------------------------------------------
    # PARSE SQL
    # ------------------------------------------------------------------

    def _parse_sql(self):

        try:

            self.parsed = sqlglot.parse_one(
                self.original_sql,
                read="oracle"
            )

        except Exception as e:

            raise ValueError(
                f"\nFailed to parse SQL:\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # CHECK CTE
    # ------------------------------------------------------------------

    def has_cte(self):

        return bool(
            self.parsed.find(exp.With)
        )

    # ------------------------------------------------------------------
    # EXTRACT CTES
    # ------------------------------------------------------------------

    def extract_ctes(self):

        ctes = []

        with_clause = self.parsed.find(exp.With)

        if not with_clause:

            return ctes

        for cte in with_clause.expressions:

            try:

                cte_name = cte.alias_or_name

                cte_sql = cte.this.sql(
                    dialect="oracle",
                    pretty=True
                )

                ctes.append({
                    "name": cte_name,
                    "sql": cte_sql
                })

            except Exception as e:

                print("\nFAILED TO PARSE CTE")
                print(str(e))

        return ctes

    # ------------------------------------------------------------------
    # EXTRACT FINAL QUERY
    # ------------------------------------------------------------------

    def extract_final_query(self):

        """
        Extract inner SELECT query from:

        Pattern 1 (non-function scenarios):
            SELECT ot.*
            FROM (
                WITH ...
                SELECT ...
            ) ot

        Pattern 2 (function scenarios):
            WITH CTE_NAME AS (...)
            SELECT ...  <-- this part

        Returns ONLY the final SELECT after all CTEs.
        """

        try:

            sql = self.original_sql

            # Strip -- comments (function SQL files have header comments)
            sql_no_comments = "\n".join(
                line for line in sql.splitlines()
                if not line.strip().startswith("--")
            ).strip()

            # ----------------------------------------------------------
            # PATTERN 1: SELECT ot.* FROM (WITH ... SELECT ...) ot
            # ----------------------------------------------------------

            from_pos = re.search(
                r'FROM\s*\(',
                sql_no_comments,
                re.IGNORECASE
            )

            if from_pos:

                content = sql_no_comments[from_pos.end():]

                end_match = re.search(
                    r'\)\s*ot\b',
                    content,
                    re.IGNORECASE
                )

                if end_match:

                    content = content[:end_match.start()]

                    with_pos = re.search(
                        r'\bWITH\b',
                        content,
                        re.IGNORECASE
                    )

                    if with_pos:

                        content = content[with_pos.end():]

                    bracket_count = 0
                    i = 0

                    while i < len(content):

                        char = content[i]

                        if char == "(":
                            bracket_count += 1

                        elif char == ")":
                            bracket_count -= 1

                        if (
                            bracket_count == 0
                            and content[i:i+6].upper() == "SELECT"
                        ):

                            final_query = content[i:]

                            return final_query.strip()

                        i += 1

            # ----------------------------------------------------------
            # PATTERN 2: WITH CTE AS (...) final_select
            # Fallback for function scenarios where SQL is:
            #   WITH All_Trxn_B AS ( ... ) SELECT ... FROM ...
            # ----------------------------------------------------------

            stripped = sql_no_comments
            with_match = re.match(r'\bWITH\s+', stripped, re.IGNORECASE)

            if with_match:

                after_with = stripped[with_match.end():]

                cte_close = self._find_last_cte_closing_paren(after_with)

                if cte_close is not None and cte_close + 1 < len(after_with):

                    remaining = after_with[cte_close + 1:].strip()

                    select_match = re.match(r'\bSELECT\b', remaining, re.IGNORECASE)

                    if select_match:

                        return remaining.strip()

            return ""

        except Exception as e:

            print("\nFAILED TO EXTRACT FINAL QUERY")
            print(str(e))

            return ""

    def _find_last_cte_closing_paren(self, sql: str) -> int:
        """
        Find the index of the closing ')' that ends the last CTE definition.
        Handles multiple CTEs: WITH a AS (...), b AS (...) <-- this paren
        Returns the position relative to `sql`, or None if not found.
        """
        depth = 0
        i = 0
        last_close_at_depth_zero = None

        while i < len(sql):
            ch = sql[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    last_close_at_depth_zero = i
                    after = sql[i + 1:].lstrip()
                    if re.match(r'\bSELECT\b', after, re.IGNORECASE):
                        return last_close_at_depth_zero
            i += 1

        return last_close_at_depth_zero
    # ------------------------------------------------------------------
    # EXTRACT DEPENDENCIES
    # ------------------------------------------------------------------

    def extract_dependencies(self):

        dependencies = {}

        ctes = self.extract_ctes()

        cte_names = {
            cte["name"].lower()
            for cte in ctes
        }

        for cte in ctes:

            current_name = cte["name"]

            sql_text = cte["sql"]

            try:

                parsed_cte = sqlglot.parse_one(
                    sql_text,
                    read="oracle"
                )

                tables = set()

                for table in parsed_cte.find_all(exp.Table):

                    table_name = table.name.lower()

                    if table_name in cte_names:

                        tables.add(table_name)

                dependencies[current_name] = sorted(
                    list(tables)
                )

            except Exception as e:

                print(
                    f"\nDependency extraction failed "
                    f"for {current_name}"
                )

                print(str(e))

                dependencies[current_name] = []

        return dependencies


# ----------------------------------------------------------------------
# LOAD SQL FILE
# ----------------------------------------------------------------------

def load_sql_file(file_path, run_logger=None, step=2):

    if run_logger:
        run_logger.log(step, f"Loading SQL file: {file_path}")

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as f:

        sql_text = f.read()

    if run_logger:
        run_logger.log(step, f"Loaded SQL Length: {len(sql_text)} chars")

    return sql_text


# ----------------------------------------------------------------------
# FIND LATEST DATASET QUERY
# ----------------------------------------------------------------------

def get_latest_dataset_query():

    latest_output_dir = get_latest_output_directory()

    extracted_dir = os.path.join(
        latest_output_dir,
        "extracted_queries"
    )

    if not os.path.exists(extracted_dir):

        raise FileNotFoundError(
            f"\nDirectory not found:\n"
            f"{extracted_dir}"
        )

    dataset_files = [

        os.path.join(extracted_dir, f)

        for f in os.listdir(extracted_dir)

        if ("dataset_query" in f.lower() or "dataset_" in f.lower())
        and f.lower().endswith(".sql")
    ]

    if not dataset_files:

        raise FileNotFoundError(
            "\nNo dataset query files found"
        )

    latest_file = max(
        dataset_files,
        key=os.path.getmtime
    )

    return latest_file


# ----------------------------------------------------------------------
# SAVE OUTPUTS
# ----------------------------------------------------------------------

def save_outputs(
    ctes,
    final_query,
    dependencies,
    output_dir,
    run_logger=None,
    step=2
):

    parsed_dir = os.path.join(
        output_dir,
        "parsed_ctes"
    )

    os.makedirs(
        parsed_dir,
        exist_ok=True
    )

    # --------------------------------------------------------------
    # SAVE CTE FILES
    # --------------------------------------------------------------

    for idx, cte in enumerate(ctes, start=1):

        cte_name = cte["name"]

        safe_name = re.sub(
            r'[^a-zA-Z0-9_]',
            '_',
            cte_name
        )

        file_path = os.path.join(
            parsed_dir,
            f"{idx:03d}_{safe_name}.sql"
        )

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                f"-- CTE NAME: {cte_name}\n\n"
            )

            f.write(cte["sql"])

        if run_logger:
            run_logger.log(step, f"Saved CTE {idx}: {cte_name} -> {file_path}")

    # --------------------------------------------------------------
    # SAVE FINAL QUERY
    # --------------------------------------------------------------

    final_query_file = os.path.join(
        parsed_dir,
        "final_query.sql"
    )

    with open(
        final_query_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(final_query)

    # --------------------------------------------------------------
    # SAVE DEPENDENCIES
    # --------------------------------------------------------------

    dependency_file = os.path.join(
        parsed_dir,
        "dependencies.json"
    )

    with open(
        dependency_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            dependencies,
            f,
            indent=4
        )

    if run_logger:
        run_logger.log(step, f"Saved final_query -> {final_query_file}")
        run_logger.log(step, f"Saved dependencies -> {dependency_file}")
        run_logger.summary(step, "Files Saved Summary", [
            f"Parsed Directory: {parsed_dir}",
            f"Final Query: {final_query_file}",
            f"Dependencies: {dependency_file}",
            f"Total CTEs: {len(ctes)}"
        ])
    else:
        print("\n" + "=" * 80)
        print("FILES SAVED")
        print("=" * 80)
        print(f"\nParsed Directory:\n{parsed_dir}")
        print(f"\nFinal Query:\n{final_query_file}")
        print(f"\nDependencies:\n{dependency_file}")


# ----------------------------------------------------------------------
# CALLABLE ENTRY POINT (used by pipeline.py)
# ----------------------------------------------------------------------

def parse_ctes(output_dir=None, run_logger=None):
    """
    Run the full CTE parsing pipeline and save outputs.
    Called by pipeline.py Step 4 via importlib.
    Returns the number of CTEs parsed.

    output_dir: when provided by pipeline.py, use it directly so that
    concurrent batch jobs cannot contaminate each other's paths.
    """
    step = 2
    if run_logger:
        run_logger.section("STEP 2: CTE Parser — Splitting SQL into individual CTEs")
        run_logger.log(step, "Loading: CTE Parser — splitting dataset query into CTEs")

    if output_dir is None:
        output_dir = get_latest_output_directory()

    # Find the dataset query file inside this specific output directory
    extracted_dir = os.path.join(output_dir, "extracted_queries")
    dataset_files = [
        os.path.join(extracted_dir, f)
        for f in os.listdir(extracted_dir)
        if ("dataset_query" in f.lower() or "dataset_" in f.lower() or "resolved_function_dataset_" in f.lower()) and f.lower().endswith(".sql")
    ] if os.path.isdir(extracted_dir) else []

    if not dataset_files:
        # Fallback to old global search
        sql_file = get_latest_dataset_query()
    else:
        sql_file = max(dataset_files, key=os.path.getmtime)

    sql_text = load_sql_file(sql_file, run_logger=run_logger, step=step)

    if run_logger:
        run_logger.log_sql(step, "source_dataset_query_for_parsing", sql_text)

    parser = CTEParser(sql_text)

    has_cte = parser.has_cte()
    if run_logger:
        run_logger.log(step, f"Has CTE clause: {has_cte}")

    ctes         = parser.extract_ctes()
    final_query  = parser.extract_final_query()
    dependencies = parser.extract_dependencies()

    cte_names = [c["name"] for c in ctes]
    if run_logger:
        run_logger.log(step, f"Parsed {len(ctes)} CTEs: {cte_names}")
        run_logger.log_block(step, "CTE Dependencies", dependencies)

    save_outputs(ctes, final_query, dependencies, output_dir, run_logger=run_logger, step=step)

    if run_logger:
        run_logger.log(step, f"CTE parsing complete — {len(ctes)} CTEs saved to parsed_ctes/")

    return len(ctes)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

if __name__ == "__main__":

    try:

        print("\n" + "=" * 80)
        print("CTE PARSER")
        print("=" * 80)

        # --------------------------------------------------------------
        # LOAD QUERY
        # --------------------------------------------------------------

        sql_file = get_latest_dataset_query()

        print(f"\nUsing SQL File:\n{sql_file}")

        sql_text = load_sql_file(sql_file)

        latest_output_dir = get_latest_output_directory()

        # --------------------------------------------------------------
        # PARSE SQL
        # --------------------------------------------------------------

        parser = CTEParser(sql_text)

        print(
            f"\nHas CTE: {parser.has_cte()}"
        )

        # --------------------------------------------------------------
        # EXTRACT CTES
        # --------------------------------------------------------------

        ctes = parser.extract_ctes()

        print("\n" + "=" * 80)
        print("EXTRACTED CTES")
        print("=" * 80)

        print(
            f"\nTotal CTEs Found: "
            f"{len(ctes)}"
        )

        for idx, cte in enumerate(ctes, start=1):

            print("\n" + "-" * 80)

            print(f"\nCTE #{idx}")

            print(f"\nCTE Name:\n{cte['name']}")

            print("\nCTE SQL Preview:\n")

            print(cte["sql"][:1000])

        # --------------------------------------------------------------
        # FINAL QUERY
        # --------------------------------------------------------------

        final_query = parser.extract_final_query()

        print("\n" + "=" * 80)
        print("FINAL QUERY")
        print("=" * 80)

        print(final_query[:5000])

        # --------------------------------------------------------------
        # DEPENDENCIES
        # --------------------------------------------------------------

        dependencies = parser.extract_dependencies()

        print("\n" + "=" * 80)
        print("CTE DEPENDENCIES")
        print("=" * 80)

        for cte_name, deps in dependencies.items():

            print(f"\n{cte_name}")

            if deps:

                print("Depends On:")
                print(deps)

            else:

                print("No Dependencies")

        # --------------------------------------------------------------
        # SAVE OUTPUTS
        # --------------------------------------------------------------

        save_outputs(
            ctes,
            final_query,
            dependencies,
            latest_output_dir
        )

    except Exception as e:

        print("\n" + "=" * 80)
        print("ERROR")
        print("=" * 80)

        print(str(e))