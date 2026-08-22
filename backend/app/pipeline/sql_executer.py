import os
import logging
import subprocess
import sys

from dotenv import load_dotenv

from db_connect import connect_to_oracle

# ------------------------------------------------------------------
# LOAD ENV
# ------------------------------------------------------------------

load_dotenv()

# ------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# FIND LATEST DATASET QUERY FILE
# ------------------------------------------------------------------


def get_latest_dataset_query_file() -> str:
    """
    Find latest dataset query SQL file from:

    OUTPUT_BASE_PATH/
        YYYYMMDD/
            SCENARIO/
                extracted_queries/
    """

    base_path = os.getenv(
        "OUTPUT_BASE_PATH",
        "ofsaa_outputs"
    )

    if not os.path.exists(base_path):

        raise FileNotFoundError(
            f"Output path not found: {base_path}"
        )

    dataset_files = []

    # Walk all folders
    for root, dirs, files in os.walk(base_path):

        for file in files:

            lower_file = file.lower()

            if (
                ("dataset_query" in lower_file or "dataset_" in lower_file)
                and lower_file.endswith(".sql")
            ):

                dataset_files.append(
                    os.path.join(root, file)
                )

    if not dataset_files:

        raise FileNotFoundError(
            "No dataset query files found"
        )

    latest_file = max(
        dataset_files,
        key=os.path.getmtime
    )

    return latest_file


# ------------------------------------------------------------------
# LOAD SQL FILE
# ------------------------------------------------------------------


def load_sql_file(file_path: str) -> str:
    """
    Load SQL query from file.
    """

    logger.info(
        "Loading SQL file: %s",
        file_path
    )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as f:

        sql = f.read()

    # Remove comments
    cleaned_lines = []

    for line in sql.splitlines():

        stripped = line.strip()

        if stripped.startswith("--"):

            continue

        cleaned_lines.append(line)

    sql = "\n".join(cleaned_lines).strip()

    if not sql:

        raise ValueError(
            "SQL file is empty"
        )

    logger.info(
        "SQL loaded successfully (%d chars)",
        len(sql)
    )

    return sql


# ------------------------------------------------------------------
# EXECUTE DATASET QUERY
# ------------------------------------------------------------------


def execute_dataset_query(
    conn,
    sql_query: str,
    run_logger=None
) -> bool:
    """
    Execute dataset query.

    Returns:
        True  -> Alerts/data found
        False -> No data found
    """

    cursor = conn.cursor()

    try:

        if run_logger:
            run_logger.log(1, "Loading: Run whole dataset query — checking if alerts exist")
            run_logger.log_sql(1, "dataset_query", sql_query)
        else:
            logger.info("[STEP 1] Executing dataset query...")

        cursor.execute(sql_query)

        rows = cursor.fetchmany(10)

        # ----------------------------------------------------------
        # ALERTS FOUND
        # ----------------------------------------------------------

        if rows:

            if run_logger:
                run_logger.summary(1, "Result: ALERTS FOUND", [
                    f"Returned {len(rows)} rows",
                    "Scenario is working correctly — alerts are being generated.",
                ])
            else:
                print("\n" + "=" * 80)
                print("ALERTS FOUND")
                print("=" * 80)
                print(f"\nReturned {len(rows)} rows")

            for idx, row in enumerate(rows, start=1):
                if not run_logger:
                    print("\n" + "-" * 80)
                    print(f"ROW {idx}")
                    print("-" * 80)
                    print(row)

            return True

        # ----------------------------------------------------------
        # NO ALERTS
        # ----------------------------------------------------------

        else:

            if run_logger:
                run_logger.log(1, "Result: 0 rows — NO ALERTS generated")
                run_logger.log(1, "Proceeding to Step 2: CTE parsing and execution")
            else:
                print("\n" + "=" * 80)
                print("NO ALERTS FOUND")
                print("=" * 80)

            return False

    finally:

        cursor.close()


# ------------------------------------------------------------------
# TRIGGER CTE PARSER
# ------------------------------------------------------------------


def trigger_cte_parser():

    print("\n" + "=" * 80)
    print("TRIGGERING CTE PARSER")
    print("=" * 80)

    subprocess.run(
        [sys.executable, "cte_parser.py"],
        check=True
    )


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------

if __name__ == "__main__":

    conn = None

    try:

        print("\n" + "=" * 80)
        print("SQL EXECUTER")
        print("=" * 80)

        # ----------------------------------------------------------
        # CONNECT TO DATABASE
        # ----------------------------------------------------------

        logger.info(
            "Connecting to Oracle database..."
        )

        conn = connect_to_oracle()

        if conn is None:

            raise ConnectionError(
                "Oracle connection failed"
            )

        logger.info(
            "Database connection successful"
        )

        # ----------------------------------------------------------
        # LOAD LATEST DATASET QUERY
        # ----------------------------------------------------------

        latest_sql_file = (
            get_latest_dataset_query_file()
        )

        print(
            f"\nUsing Dataset Query File:\n"
            f"{latest_sql_file}"
        )

        sql_query = load_sql_file(
            latest_sql_file
        )

        # ----------------------------------------------------------
        # EXECUTE QUERY
        # ----------------------------------------------------------

        has_alerts = execute_dataset_query(
            conn,
            sql_query
        )

        # ----------------------------------------------------------
        # NO ALERTS → RUN CTE PARSER
        # ----------------------------------------------------------

        if not has_alerts:

            trigger_cte_parser()

    # ------------------------------------------------------------------
    # FAILURE CASE
    # ------------------------------------------------------------------

    except Exception as e:

        print("\n" + "=" * 80)
        print("SQL EXECUTION FAILED")
        print("=" * 80)

        print(str(e))

        logger.exception(
            "Execution failed"
        )

        # ----------------------------------------------------------
        # RUN CTE PARSER ON FAILURE
        # ----------------------------------------------------------

        try:

            trigger_cte_parser()

        except Exception as sub_err:

            print(
                "\nFailed to run cte_parser.py"
            )

            print(str(sub_err))

    finally:

        if conn:

            conn.close()

            logger.info(
                "Database connection closed"
            )