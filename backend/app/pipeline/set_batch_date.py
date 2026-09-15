# set_batch_date.py

import os
import json
import logging
import paramiko

from datetime import datetime
from dotenv import load_dotenv

from path_manager import (
    get_latest_output_directory
)

# ----------------------------------------------------------------------
# LOAD ENV
# ----------------------------------------------------------------------

load_dotenv()

# ----------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# LOAD METADATA
# ----------------------------------------------------------------------

def load_metadata(output_dir: str = None) -> dict:
    """Reads metadata.json for the current job. When `output_dir` is given
    (the FastAPI pipeline always has one — state["output_dir"], set by the
    log-reader step), reads directly from it. Otherwise falls back to
    get_latest_output_directory()'s "most-recently-modified directory
    anywhere under OUTPUT_BASE_PATH" heuristic, for standalone
    command-line use.

    That fallback is NOT safe under concurrency: confirmed live this
    function was being called with no output_dir from the FastAPI pipeline
    too, so any other job (a background test, a second concurrent
    upload — this app doesn't serialize requests) that touched a different
    scenario's directory even a moment earlier could make this one grab
    the wrong job's metadata, or hit a bare "Metadata file not found" if
    that other directory doesn't have one yet — an intermittent, hard-to-
    explain failure with no connection to whatever file the user actually
    uploaded. Always pass output_dir from pipeline code; the fallback
    exists only for scripts run directly with no job context at all."""

    target_dir = output_dir or get_latest_output_directory()

    metadata_file = os.path.join(
        target_dir,
        "metadata.json"
    )

    if not os.path.exists(metadata_file):

        raise FileNotFoundError(
            f"\nMetadata file not found:\n"
            f"{metadata_file}"
        )

    with open(
        metadata_file,
        "r",
        encoding="utf-8"
    ) as f:

        metadata = json.load(f)

    return metadata


# ----------------------------------------------------------------------
# FORMAT BUSINESS DATE
# ----------------------------------------------------------------------

def format_business_date(
    business_date: str
) -> str:

    return datetime.strptime(
        business_date,
        "%Y-%m-%d"
    ).strftime("%Y%m%d")


# ----------------------------------------------------------------------
# EXECUTE SSH COMMAND
# ----------------------------------------------------------------------

def execute_command(
    ssh_client,
    command: str,
    ignore_error: bool = False
):

    print("\n" + "-" * 80)
    print(f"Executing:\n{command}")
    print("-" * 80)

    stdin, stdout, stderr = ssh_client.exec_command(
        command
    )

    exit_status = stdout.channel.recv_exit_status()

    output = stdout.read().decode()

    error = stderr.read().decode()

    combined_output = output + "\n" + error

    print("\nOUTPUT:\n")
    print(combined_output)

    # --------------------------------------------------------------
    # IGNORE STTY WARNING
    # --------------------------------------------------------------

    cleaned_output = combined_output.replace(
        "stty: 'standard input': Inappropriate ioctl for device",
        ""
    )

    # --------------------------------------------------------------
    # HANDLE FAILURES
    # --------------------------------------------------------------

    fatal_error = (
        "[FATAL]" in cleaned_output
        or "Error occured during Execution" in cleaned_output
    )

    # Ignore no batch running message

    no_batch_running = (
        "No Batch Process is currently running"
        in cleaned_output
    )

    if fatal_error and not no_batch_running:

        if ignore_error:

            print(
                "\nWARNING: Command failed but continuing..."
            )

        else:

            # The remote script's own [FATAL] message (e.g. "Some Batch
            # Process is already running") is the actual, actionable
            # reason — it was being captured into cleaned_output above and
            # printed to the server console, but discarded here, so the
            # user only ever saw the bare command with no explanation.
            # Confirmed live: this is what was hiding a real, transient
            # OFSAA-side state issue (a concurrent/stuck batch on a shared
            # environment) behind an undiagnosable error.
            reason = cleaned_output.strip() or "(no output captured)"

            raise RuntimeError(
                f"\nCommand failed:\n{command}\n\nServer response:\n{reason}"
            )


# ----------------------------------------------------------------------
# CHECK CURRENT BATCH DATE
# ----------------------------------------------------------------------

def _get_current_batch_date():
    """Reads KDD_PRCSNG_BATCH_CONTROL.DATA_DUMP_DT — the business date the
    remote OFSAA batch is ALREADY configured for, if any. Returns a
    datetime.date, or None if the check itself fails or no row exists
    (defensive: if we can't determine the current state, the caller should
    fall through to the normal end/set/start sequence rather than silently
    skip it — never guess that a skip is safe)."""
    from db_connect import connect_to_oracle

    try:
        conn = connect_to_oracle()
    except Exception as e:
        print(f"\nCould not check current batch date (proceeding with SSH batch-date set): {e}")
        return None

    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT DATA_DUMP_DT FROM kdd_prcsng_batch_control")
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].date()
        finally:
            cursor.close()
    except Exception as e:
        print(f"\nCould not check current batch date (proceeding with SSH batch-date set): {e}")
    finally:
        conn.close()

    return None


# ----------------------------------------------------------------------
# SET BATCH DATE
# ----------------------------------------------------------------------

def set_batch_date(output_dir: str = None):

    # --------------------------------------------------------------
    # LOAD METADATA
    # --------------------------------------------------------------

    metadata = load_metadata(output_dir)

    business_date = metadata.get(
        "current_business_date"
    )

    if not business_date:

        raise ValueError(
            "\ncurrent_business_date not found"
        )

    # --------------------------------------------------------------
    # SKIP ENTIRELY IF THE REMOTE BATCH IS ALREADY SET TO THIS DATE
    # --------------------------------------------------------------
    # The remote OFSAA batch control is a single shared resource — running
    # end/set/start again when it's already correctly configured doesn't
    # just waste ~30-40s of SSH round trips, it FAILS outright with "Some
    # Batch Process is already running" (confirmed live, repeatedly) since
    # start_mantas_batch.sh refuses to start a second time. This is the
    # normal case whenever two requests for the SAME scenario/date run
    # close together (re-analyzing, re-tuning, a second team member) — not
    # a genuine conflict needing to be reported as an error at all.

    required_date = datetime.strptime(business_date, "%Y-%m-%d").date()
    current_batch_date = _get_current_batch_date()

    if current_batch_date == required_date:
        print(
            f"\nRemote batch is already set to the required date ({required_date}) — "
            f"skipping end/set/start entirely."
        )
        return

    formatted_business_date = (
        format_business_date(
            business_date
        )
    )

    print("\nOriginal Business Date:")
    print(business_date)

    print("\nFormatted Business Date:")
    print(formatted_business_date)

    # --------------------------------------------------------------
    # SERVER DETAILS
    # --------------------------------------------------------------

    SERVER_HOST = os.getenv("SERVER_HOST")
    SERVER_PORT = int(
        os.getenv("SERVER_PORT", "22")
    )

    SERVER_USERNAME = os.getenv(
        "SERVER_USERNAME"
    )

    SERVER_PASSWORD = os.getenv(
        "SERVER_PASSWORD"
    )

    MANTAS_BATCH_PATH = os.getenv(
        "MANTAS_BATCH_PATH"
    )

    if not all([
        SERVER_HOST,
        SERVER_USERNAME,
        SERVER_PASSWORD,
        MANTAS_BATCH_PATH
    ]):

        raise ValueError(
            "\nMissing server configuration in .env"
        )

    # --------------------------------------------------------------
    # CONNECT SSH
    # --------------------------------------------------------------

    print("\n" + "=" * 80)
    print("CONNECTING TO SERVER")
    print("=" * 80)

    ssh = paramiko.SSHClient()

    ssh.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    ssh.connect(
        hostname=SERVER_HOST,
        port=SERVER_PORT,
        username=SERVER_USERNAME,
        password=SERVER_PASSWORD
    )

    print("\nSSH Connection Successful")

    # --------------------------------------------------------------
    # BASE COMMAND
    # --------------------------------------------------------------

    base_cmd = (
        "cd /home/oracle && "
        "source .profile >/dev/null 2>&1 && "
        f"cd {MANTAS_BATCH_PATH} && "
    )

    # --------------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------------

    end_batch_cmd = (
        base_cmd +
        "./end_mantas_batch.sh DLY"
    )

    set_date_cmd = (
        base_cmd +
        f"./set_mantas_date.sh {formatted_business_date}"
    )

    start_batch_cmd = (
        base_cmd +
        "./start_mantas_batch.sh DLY"
    )

    try:

        # ----------------------------------------------------------
        # END BATCH
        # ----------------------------------------------------------

        execute_command(
            ssh,
            end_batch_cmd,
            ignore_error=True
        )

        # ----------------------------------------------------------
        # SET DATE
        # ----------------------------------------------------------

        execute_command(
            ssh,
            set_date_cmd
        )

        # ----------------------------------------------------------
        # START BATCH
        # ----------------------------------------------------------

        execute_command(
            ssh,
            start_batch_cmd
        )

        print("\n" + "=" * 80)
        print("BATCH DATE UPDATED SUCCESSFULLY")
        print("=" * 80)

    finally:

        ssh.close()

        print("\nSSH Connection Closed")


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

if __name__ == "__main__":

    try:

        set_batch_date()

    except Exception as e:

        print("\n" + "=" * 80)
        print("ERROR")
        print("=" * 80)
        print(str(e))