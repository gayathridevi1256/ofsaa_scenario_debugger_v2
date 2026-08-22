import os
import re

from datetime import datetime
from dotenv import load_dotenv

# ----------------------------------------------------------------------
# LOAD ENV
# ----------------------------------------------------------------------

load_dotenv()

# ----------------------------------------------------------------------
# CLEAN FOLDER NAME
# ----------------------------------------------------------------------

def sanitize_name(name: str) -> str:
    """
    Remove invalid folder characters.
    """

    if not name:

        return "UNKNOWN_SCENARIO"

    name = re.sub(
        r'[^a-zA-Z0-9_-]',
        '_',
        name
    )

    return name.strip("_")


# ----------------------------------------------------------------------
# CREATE OUTPUT DIRECTORY
# ----------------------------------------------------------------------

def create_output_directory(
    job_description: str
) -> str:
    """
    Create folder structure:

    OUTPUT_BASE_PATH/
        YYYYMMDD/
            JOB_DESCRIPTION/
    """

    base_path = os.getenv(
        "OUTPUT_BASE_PATH",
        "ofsaa_outputs"
    )

    today = datetime.now().strftime(
        "%Y%m%d"
    )

    scenario_name = sanitize_name(
        job_description
    )

    output_dir = os.path.join(
        base_path,
        today,
        scenario_name
    )

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    return output_dir


# ----------------------------------------------------------------------
# GET LATEST OUTPUT DIRECTORY
# ----------------------------------------------------------------------

def get_latest_output_directory() -> str:
    """
    Finds the most-recently-modified scenario output directory.
    Used by standalone pipeline scripts (cte_parser.py, cte_executer.py)
    when run directly from the command line.

    When called from the FastAPI pipeline, each step receives output_dir
    explicitly via state["output_dir"] — this function is NOT called in
    that path, so there is no cross-job contamination risk.

    Example:
    ofsaa_outputs/
        20260526/
            Scenario_ABC/
    """
    base_path = os.getenv(
        "OUTPUT_BASE_PATH",
        "ofsaa_outputs"
    )

    if not os.path.exists(base_path):

        raise FileNotFoundError(
            f"\nOutput base path not found:\n"
            f"{base_path}"
        )

    # --------------------------------------------------------------
    # FIND LATEST DATE FOLDER
    # --------------------------------------------------------------

    date_dirs = [

        os.path.join(base_path, d)

        for d in os.listdir(base_path)

        if os.path.isdir(
            os.path.join(base_path, d)
        )
    ]

    if not date_dirs:

        raise FileNotFoundError(
            "\nNo date folders found"
        )

    latest_date_dir = max(
        date_dirs,
        key=os.path.getmtime
    )

    # --------------------------------------------------------------
    # FIND LATEST SCENARIO FOLDER
    # --------------------------------------------------------------

    scenario_dirs = [

        os.path.join(latest_date_dir, d)

        for d in os.listdir(latest_date_dir)

        if os.path.isdir(
            os.path.join(latest_date_dir, d)
        )
    ]

    if not scenario_dirs:

        raise FileNotFoundError(
            "\nNo scenario folders found"
        )

    latest_scenario_dir = max(
        scenario_dirs,
        key=os.path.getmtime
    )

    return latest_scenario_dir