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

def load_metadata() -> dict:

    latest_output_dir = (
        get_latest_output_directory()
    )

    metadata_file = os.path.join(
        latest_output_dir,
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

            raise RuntimeError(
                f"\nCommand failed:\n{command}"
            )


# ----------------------------------------------------------------------
# SET BATCH DATE
# ----------------------------------------------------------------------

def set_batch_date():

    # --------------------------------------------------------------
    # LOAD METADATA
    # --------------------------------------------------------------

    metadata = load_metadata()

    business_date = metadata.get(
        "current_business_date"
    )

    if not business_date:

        raise ValueError(
            "\ncurrent_business_date not found"
        )

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