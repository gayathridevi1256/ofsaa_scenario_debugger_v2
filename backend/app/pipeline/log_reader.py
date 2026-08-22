import logging
import re
import os
from datetime import datetime
from scenario_config import MAX_SQL_FILE_BYTES, FUNCTION_SCENARIO_IDS
from path_manager import create_output_directory
import json

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# FLOW DETECTION
# ----------------------------------------------------------------------

def _is_function_scenario(metadata: dict) -> bool:
    """Check if scenario ID requires multi-query flow (function extraction)."""
    scnro_id = metadata.get("scnro_id", "")
    return scnro_id in FUNCTION_SCENARIO_IDS

# ----------------------------------------------------------------------
# FUNCTION EXTRACTION
# ----------------------------------------------------------------------

FUNCTION_PATTERN = re.compile(r'fccmatomic\.(F_\w+|ANOM\w+)', re.IGNORECASE)


def _extract_functions_from_first_queries(queries: dict) -> list[str]:
    """Extract function names from first reference and first dataset query only."""
    first_ref = queries.get("main", "")
    first_ds = queries.get("dataset", "")
    all_sql = first_ref + "\n" + first_ds
    return list(set(FUNCTION_PATTERN.findall(all_sql)))


def _extract_function_body_from_db(conn, function_name: str) -> str | None:
    """Query ALL_SOURCE for function body between IS/AS and ; using XMLAGG."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT XMLCAST(
                 XMLAGG(
                   XMLELEMENT(e, TEXT)
                   ORDER BY LINE
                 ) AS CLOB
               ) AS FUNCTION_SOURCE
            FROM ALL_SOURCE
            WHERE NAME = UPPER(:name)
              AND TYPE = 'FUNCTION'
        """, [function_name])

        result = cursor.fetchone()
        if not result or not result[0]:
            return None

        # CLOB needs .read() to convert to string
        full_text = result[0].read() if hasattr(result[0], 'read') else result[0]

        # Extract body between IS/AS and END <name>;
        # Use position-based extraction to avoid non-greedy regex issues
        start_match = re.search(r'(?:IS|AS)\s+', full_text, re.IGNORECASE)
        if not start_match:
            return None
        start = start_match.end()

        # Find END <function_name>; or just END;
        end_match = re.search(r'\s*END\s+\w+\s*;', full_text, re.IGNORECASE)
        if not end_match:
            end_match = re.search(r'\s*END\s*;', full_text, re.IGNORECASE)
        if not end_match:
            return None
        end = end_match.start()

        body = full_text[start:end].strip()
        return body if body else None
    finally:
        cursor.close()


def _save_functions_to_files(functions, conn, output_dir, timestamp):
    """Extract and save each function body as functionname.sql."""
    files_saved = {}
    for func_name in functions:
        func_body = _extract_function_body_from_db(conn, func_name)
        if func_body:
            func_file = os.path.join(output_dir, f"{func_name}_{timestamp}.sql")
            with open(func_file, "w", encoding="utf-8") as f:
                f.write(f"-- FUNCTION: {func_name}\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(func_body)
            files_saved[func_name] = func_file
            print(f"\nSaved Function:\n{func_file}")
    return files_saved


# ----------------------------------------------------------------------
# PARAMETER EXTRACTION
# ----------------------------------------------------------------------

def _extract_function_args_block(sql_text: str) -> str | None:
    """
    Extract the argument block from the first fccmatomic.F_* or fccmatomic.ANOM* function call.
    Properly handles nested parentheses in Lst(...) calls.
    """
    # Find start of function call (F_* or ANOM*)
    func_match = re.search(r'fccmatomic\.(?:F_\w+|ANOM\w+)\s*\(', sql_text, re.IGNORECASE)
    if not func_match:
        print(f"[DEBUG] _extract_function_args_block: No fccmatomic.F_*/ANOM* match in {len(sql_text)} chars")
        return None
    
    print(f"[DEBUG] _extract_function_args_block: Matched '{func_match.group()}' at pos {func_match.start()}")

    start = func_match.end() - 1  # Position of the first '('

    # Find matching closing paren
    depth = 1
    i = start + 1
    while i < len(sql_text) and depth > 0:
        if sql_text[i] == '(':
            depth += 1
        elif sql_text[i] == ')':
            depth -= 1
        i += 1

    if depth != 0:
        return None

    return sql_text[start + 1:i - 1]


def _extract_parameters_from_sql(sql_text: str) -> list[str]:
    """
    Extract parameter arguments from a function call in SQL.
    """
    args_text = _extract_function_args_block(sql_text)
    if not args_text:
        print(f"[DEBUG] _extract_parameters_from_sql: No args block found (func_match failed)")
        return []

    result = _split_function_args(args_text)
    print(f"[DEBUG] _extract_parameters_from_sql: args_text={len(args_text)} chars, split into {len(result)} args")
    return result


def _split_function_args(text: str) -> list[str]:
    """Split function arguments respecting nested parentheses and quoted strings."""
    args = []
    current = ""
    depth = 0
    in_string = False
    string_char = None

    for ch in text:
        if in_string:
            current += ch
            if ch == string_char:
                in_string = False
            continue

        if ch in ("'", '"'):
            in_string = True
            string_char = ch
            current += ch
            continue

        if ch == '(':
            depth += 1
            current += ch
        elif ch == ')':
            depth -= 1
            current += ch
        elif ch == ',' and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += ch

    if current.strip():
        args.append(current.strip())

    return args


def _param_name_from_arg(arg: str) -> str | None:
    """
    Convert a parameter argument to a key name.
    @Lrf_Digits → p_Lrf_Digits
    fccmatomic.Lst(@Incl_Src) → p_Incl_Src
    'value' or 123 → None (these are values, not keys)
    """
    arg = arg.strip()

    # Simple @param
    m = re.match(r'^@(\w+)$', arg)
    if m:
        return f"p_{m.group(1)}"

    # fccmatomic.Lst(@param)
    m = re.match(r'fccmatomic\.Lst\(\s*@(\w+)\s*\)', arg, re.IGNORECASE)
    if m:
        return f"p_{m.group(1)}"

    # fccmatomic.Lst(@param) as ColName
    m = re.match(r'fccmatomic\.Lst\(\s*@(\w+)\s*\)\s+as\s+\w+', arg, re.IGNORECASE)
    if m:
        return f"p_{m.group(1)}"

    return None  # This is a resolved value, not a key


def _value_from_arg(arg: str) -> str | None:
    """
    Extract the value from a resolved argument.
    4 → '4'
    'B' → "'B'"
    fccmatomic.Lst('Inactive') → "Lst('Inactive')"
    """
    arg = arg.strip()
    if not arg:
        return None
    # Strip fccmatomic. prefix if present
    return re.sub(r'^fccmatomic\.', '', arg, flags=re.IGNORECASE)


def _extract_parameters(first_ref_sql: str, first_ds_sql: str) -> dict:
    """
    Extract key-value parameter pairs from first reference and first dataset query.
    Keys come from reference query (@params), values from dataset query (resolved).
    """
    params = {}

    # Get args from reference query function call
    ref_args = _extract_parameters_from_sql(first_ref_sql)
    print(f"[DEBUG] _extract_parameters: ref_args={len(ref_args) if ref_args else 0}: {ref_args[:5] if ref_args else 'None'}...")

    # Get args from dataset query function call  
    ds_args = _extract_parameters_from_sql(first_ds_sql)
    print(f"[DEBUG] _extract_parameters: ds_args={len(ds_args) if ds_args else 0}: {ds_args[:5] if ds_args else 'None'}...")

    if not ref_args or not ds_args:
        print(f"[DEBUG] _extract_parameters: Missing ref_args or ds_args, returning empty")
        return params

    # Zip keys from reference args with values from dataset args
    for ref_arg, ds_arg in zip(ref_args, ds_args):
        key = _param_name_from_arg(ref_arg)
        value = _value_from_arg(ds_arg)
        if key and value is not None:
            params[key] = value

    print(f"[DEBUG] _extract_parameters: Final params count={len(params)}")
    return params


def _save_parameters_to_json(params: dict, output_dir: str, timestamp: str):
    """Save parameters as JSON file."""
    if not params:
        return

    param_file = os.path.join(
        output_dir,
        f"parameters_{timestamp}.json"
    )

    with open(param_file, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=4)

    print(f"\nSaved Parameters:\n{param_file} ({len(params)} params)")


# ----------------------------------------------------------------------
# READ LOG FILE
# ----------------------------------------------------------------------

def _read_uploaded_text(file_path: str) -> str:
    """
    Read log file from local path.
    """

    print(f"\nReading file:\n{file_path}")

    with open(file_path, "rb") as f:
        raw = f.read()

    if len(raw) > MAX_SQL_FILE_BYTES:

        raise ValueError(
            f"File too large ({len(raw) // 1024} KB). "
            f"Maximum is {MAX_SQL_FILE_BYTES // 1024} KB."
        )

    for enc in (
        "utf-8",
        "utf-8-sig",
        "latin-1",
        "cp1252"
    ):

        try:

            text = raw.decode(enc)

            print(f"\nSuccessfully decoded using: {enc}")

            print("\nFirst 5 lines of log:\n")

            for line in text.splitlines()[:5]:
                print(line)

            return text

        except (
            UnicodeDecodeError,
            LookupError
        ):

            continue

    return raw.decode(
        "utf-8",
        errors="replace"
    )


# ----------------------------------------------------------------------
# EXTRACT METADATA
# ----------------------------------------------------------------------

def _extract_log_metadata(text: str) -> dict:
    """
    Extract metadata from OFSAA log.
    """

    metadata = {}

    print("\nChecking actual lines from log:\n")

    for line in text.splitlines()[:20]:
        print(repr(line))

    meta_regex = re.compile(
        r'(?i)\b(Job\s*description|TSHLD_SET_ID|Current\s*business\s*date)\b\s*[:=]\s*(.*)'
    )

    print("\nSearching for metadata matches...\n")

    for match in meta_regex.finditer(text):

        print("MATCH FOUND:", match.group(0))

        key = (
            match.group(1)
            .strip()
            .lower()
            .replace(" ", "_")
        )

        value = match.group(2).strip()

        # --------------------------------------------------------------
        # CLEAN TSHLD_SET_ID
        # --------------------------------------------------------------

        if key == "tshld_set_id":

            tshld_match = re.search(
                r'TSHLD_SET_ID\s*=\s*(\d+)|^(\d+)',
                value,
                re.IGNORECASE
            )

            if tshld_match:

                value = (
                    tshld_match.group(1)
                    or tshld_match.group(2)
                )

        # --------------------------------------------------------------
        # CONVERT BUSINESS DATE
        # --------------------------------------------------------------

        if key == "current_business_date":

            try:

                value = datetime.strptime(
                    value,
                    "%d/%m/%Y"
                ).date()

            except ValueError:

                print(
                    f"Invalid date format: {value}"
                )

        metadata[key] = value

    # --------------------------------------------------------------
    # EXTRACT SCNRO_ID FROM LOG
    # --------------------------------------------------------------

    scnro_match = re.search(r'SCNRO_ID\s*=\s*(\d+)', text)
    if scnro_match:
        metadata["scnro_id"] = scnro_match.group(1)
        print(f"\nExtracted SCNRO_ID: {metadata['scnro_id']}")

    print("\nExtracted Metadata:")
    print(metadata)

    return metadata

def _save_metadata(
    metadata: dict,
    output_dir: str
):
    """
    Save metadata.json
    """

    metadata_file = os.path.join(
        output_dir,
        "metadata.json"
    )

    serializable_metadata = {}

    for key, value in metadata.items():

        if hasattr(value, "isoformat"):

            serializable_metadata[key] = (
                value.isoformat()
            )

        else:

            serializable_metadata[key] = value

    with open(
        metadata_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            serializable_metadata,
            f,
            indent=4
        )

    print(
        f"\nMetadata saved:\n{metadata_file}"
    )
# ----------------------------------------------------------------------
# EXTRACT ALL QUERIES
# ----------------------------------------------------------------------

def _extract_all_queries_from_log(text: str, multi_query: bool = False) -> dict:
    """
    Extract queries from log:
    - multi_query=False: Extract FIRST reference + FIRST dataset query only (old flow)
    - multi_query=True: Extract ALL reference + ALL dataset queries with IDs (new flow)

    Returns:
        {
            "main_queries": [str, ...],      # All reference queries (new) or [first] (old)
            "dataset_queries": [              # All dataset queries with IDs (new) or [first] (old)
                {"dataset_id": str, "sql": str},
                ...
            ],
            "main": str,                      # First reference query (compatibility)
            "dataset": str,                   # First dataset query (compatibility)
            "count": int
        }
    """

    queries = {
        "main_queries": [],
        "dataset_queries": [],
        "main": None,
        "dataset": None,
        "count": 0
    }

    print("\n" + "=" * 80)
    print("QUERY EXTRACTION" + (" (MULTI-QUERY)" if multi_query else ""))
    print("=" * 80)

    # ------------------------------------------------------------------
    # REFERENCE QUERIES
    # ------------------------------------------------------------------

    main_pattern = re.compile(
        r'After expansion,\s+sql\s*=\s*(SELECT.*?)(?=\n\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+(?:DEBUG|INFO|ERROR|WARN|TRACE)\s*:|\Z)',
        re.IGNORECASE | re.DOTALL
    )

    if multi_query:
        # Extract ALL reference queries
        main_matches = main_pattern.findall(text)
        for idx, main_sql in enumerate(main_matches, 1):
            main_sql = main_sql.strip()
            queries["main_queries"].append(main_sql)
            print(f"\n✅ Reference query {idx} extracted")
            print(f"   Length: {len(main_sql)} chars")
    else:
        # Extract ONLY first reference query
        main_match = main_pattern.search(text)
        if main_match:
            main_sql = main_match.group(1).strip()
            queries["main_queries"].append(main_sql)
            print(f"\n✅ Reference query 1 extracted")
            print(f"   Length: {len(main_sql)} chars")

    # Keep first for compatibility
    if queries["main_queries"]:
        queries["main"] = queries["main_queries"][0]
        queries["count"] += len(queries["main_queries"])

    if not queries["main_queries"]:
        print("\n No reference queries found")

    # ------------------------------------------------------------------
    # DATASET QUERIES
    # ------------------------------------------------------------------

    dataset_id_pattern = re.compile(
        r'Preparing to select from dataset\s+(\d+)',
        re.IGNORECASE
    )

    dataset_sql_pattern = re.compile(
        r'Preparing to select from dataset.*?\n.*?:\s*(SELECT.*?)(?=\n\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+(?:DEBUG|INFO|ERROR|WARN|TRACE)\s*:|\Z)',
        re.IGNORECASE | re.DOTALL
    )

    id_matches = list(dataset_id_pattern.finditer(text))
    sql_matches = list(dataset_sql_pattern.finditer(text))

    if multi_query:
        # Extract ALL dataset queries with IDs
        for idx, (id_match, sql_match) in enumerate(zip(id_matches, sql_matches), 1):
            dataset_id = id_match.group(1)
            dataset_sql = sql_match.group(1).strip()
            queries["dataset_queries"].append({
                "dataset_id": dataset_id,
                "sql": dataset_sql
            })
            print(f"\n✅ Dataset query {idx} extracted (ID: {dataset_id})")
            print(f"   Length: {len(dataset_sql)} chars")
    else:
        # Extract ONLY first dataset query
        if id_matches and sql_matches:
            dataset_id = id_matches[0].group(1)
            dataset_sql = sql_matches[0].group(1).strip()
            queries["dataset_queries"].append({
                "dataset_id": dataset_id,
                "sql": dataset_sql
            })
            print(f"\n✅ Dataset query 1 extracted (ID: {dataset_id})")
            print(f"   Length: {len(dataset_sql)} chars")

    # Keep first for compatibility
    if queries["dataset_queries"]:
        queries["dataset"] = queries["dataset_queries"][0]["sql"]
        queries["count"] += len(queries["dataset_queries"])

    if not queries["dataset_queries"]:
        print("\n No dataset queries found")

    return queries


# ----------------------------------------------------------------------
# SAVE QUERIES
# ----------------------------------------------------------------------

def _save_queries_to_files(
    queries: dict,
    metadata: dict,
    db_conn=None,
    multi_query: bool = False
) -> dict:
    """
    Save extracted queries and functions.
    - multi_query=False: Save first query as main_query.sql and dataset_query.sql (old flow)
    - multi_query=True: Save all queries with numbering + extract functions (new flow)
    """

    base_output_dir = create_output_directory(
        metadata.get(
            "job_description",
            "UNKNOWN_SCENARIO"
        )
    )

    output_dir = os.path.join(
        base_output_dir,
        "extracted_queries"
    )

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    files_saved = {}

    print(
        f"\nSaving queries to:\n{output_dir}"
    )

    log_name = metadata.get(
        "job_description",
        "scenario"
    )

    log_name = re.sub(
        r'[^a-zA-Z0-9_]',
        '_',
        log_name
    )

    if multi_query:
        # --------------------------------------------------------------
        # MULTI-QUERY FLOW: Save ALL reference and dataset queries
        # --------------------------------------------------------------

        # Save all reference queries
        for idx, ref_sql in enumerate(queries.get("main_queries", []), 1):
            ref_file = os.path.join(
                output_dir,
                f"{log_name}_reference_{idx:02d}_{timestamp}.sql"
            )

            with open(
                ref_file,
                "w",
                encoding="utf-8"
            ) as f:
                f.write(f"-- REFERENCE QUERY {idx}\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(ref_sql)

            files_saved[f"reference_{idx}"] = ref_file
            print(f"\nSaved Reference Query {idx}:\n{ref_file}")

        # Save all dataset queries with IDs
        for dq in queries.get("dataset_queries", []):
            dataset_id = dq["dataset_id"]
            dataset_sql = dq["sql"]

            dataset_file = os.path.join(
                output_dir,
                f"{log_name}_dataset_{dataset_id}_{timestamp}.sql"
            )

            with open(
                dataset_file,
                "w",
                encoding="utf-8"
            ) as f:
                f.write(f"-- DATASET QUERY (ID: {dataset_id})\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(dataset_sql)

            files_saved[f"dataset_{dataset_id}"] = dataset_file
            print(f"\nSaved Dataset Query (ID: {dataset_id}):\n{dataset_file}")

        # Extract and save functions
        print(f"[DEBUG] multi_query={multi_query}, db_conn={'set' if db_conn else 'None'}")
        print(f"[DEBUG] queries keys: {list(queries.keys())}")
        print(f"[DEBUG] main_queries count: {len(queries.get('main_queries', []))}")
        print(f"[DEBUG] dataset_queries count: {len(queries.get('dataset_queries', []))}")
        print(f"[DEBUG] 'main' key exists: {'main' in queries and queries['main'] is not None}")
        print(f"[DEBUG] 'dataset' key exists: {'dataset' in queries and queries['dataset'] is not None}")
        
        if db_conn:
            functions = _extract_functions_from_first_queries(queries)
            print(f"\n[DEBUG] Function extraction: found {len(functions)} function(s): {functions}")
            if functions:
                print(f"\nFound {len(functions)} function(s): {functions}")
                func_files = _save_functions_to_files(functions, db_conn, output_dir, timestamp)
                print(f"[DEBUG] Saved {len(func_files)} function file(s): {list(func_files.keys())}")
                files_saved.update(func_files)
            else:
                print(f"[DEBUG] WARNING: No function names found in SQL")
        else:
            print(f"[DEBUG] WARNING: db_conn is None - cannot extract functions from Oracle")

        # Extract and save parameter key-value pairs
        first_ref = queries.get("main", "")
        first_ds = queries.get("dataset", "")
        print(f"[DEBUG] Parameter extraction: first_ref={len(first_ref)} chars, first_ds={len(first_ds)} chars")
        if first_ref and first_ds:
            params = _extract_parameters(first_ref, first_ds)
            print(f"[DEBUG] Extracted {len(params)} parameters: {list(params.keys()) if params else 'None'}")
            if params:
                _save_parameters_to_json(params, output_dir, timestamp)
            else:
                print(f"[DEBUG] WARNING: No parameters extracted from SQL")
        else:
            print(f"[DEBUG] WARNING: Missing first_ref or first_ds - cannot extract parameters")
    else:
        # --------------------------------------------------------------
        # SINGLE-QUERY FLOW: Save first query only (old naming)
        # --------------------------------------------------------------

        # Save first reference query as main_query.sql
        if queries.get("main"):
            main_file = os.path.join(
                output_dir,
                f"{log_name}_main_query_{timestamp}.sql"
            )

            with open(
                main_file,
                "w",
                encoding="utf-8"
            ) as f:
                f.write("-- REFERENCE QUERY\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(queries["main"])

            files_saved["main"] = main_file
            print(f"\nSaved Main Query:\n{main_file}")

        # Save first dataset query as dataset_query.sql
        if queries.get("dataset"):
            dataset_file = os.path.join(
                output_dir,
                f"{log_name}_dataset_query_{timestamp}.sql"
            )

            with open(
                dataset_file,
                "w",
                encoding="utf-8"
            ) as f:
                f.write("-- DATASET QUERY\n")
                f.write(f"-- Extracted: {datetime.now()}\n\n")
                f.write(queries["dataset"])

            files_saved["dataset"] = dataset_file
            print(f"\nSaved Dataset Query:\n{dataset_file}")

    return files_saved


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

if __name__ == "__main__":

    file_path = "RMF_AMEA.log"

    try:

        print("\n" + "=" * 80)
        print("OFSAA LOG PARSER")
        print("=" * 80)

        # --------------------------------------------------------------
        # READ LOG
        # --------------------------------------------------------------

        log_text = _read_uploaded_text(
            file_path
        )

        # --------------------------------------------------------------
        # EXTRACT METADATA
        # --------------------------------------------------------------

        metadata = _extract_log_metadata(
            log_text
        )

        print("\nMetadata:")
        print(metadata)

        # --------------------------------------------------------------
        # EXTRACT QUERIES
        # --------------------------------------------------------------

        queries = _extract_all_queries_from_log(
            log_text
        )

        print(
            f"\nTotal Queries Extracted: "
            f"{queries['count']}"
        )

        # --------------------------------------------------------------
        # SAVE QUERIES
        # --------------------------------------------------------------

        if queries["count"] > 0:

            query_files = (
                _save_queries_to_files(
                    queries,
                    metadata
                )
            )

            print("\nQueries Saved:")

            for query_type, path in (
                query_files.items()
            ):

                print(
                    f"\n{query_type}:"
                    f"\n{path}"
                )

        else:

            print(
                "\nNo queries extracted."
            )

        # --------------------------------------------------------------
        # CREATE OUTPUT DIRECTORY
        # --------------------------------------------------------------

        base_output_dir = create_output_directory(
            metadata.get(
                "job_description",
                "UNKNOWN_SCENARIO"
            )
        )

        # --------------------------------------------------------------
        # SAVE METADATA
        # --------------------------------------------------------------

        _save_metadata(
            metadata,
            base_output_dir
        )    

    except Exception as e:

        print("\n" + "=" * 80)
        print("ERROR")
        print("=" * 80)

        print(str(e))