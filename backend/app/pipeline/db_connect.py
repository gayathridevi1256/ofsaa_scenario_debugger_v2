import os
import time
import logging
import oracledb
from dotenv import load_dotenv

# ------------------------------------------------------------------
# LOAD ENV VARIABLES
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
# CONFIG FROM .env
# ------------------------------------------------------------------

DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")

DB_CONNECT_TIMEOUT_S = int(
    os.getenv("DB_CONNECT_TIMEOUT_S", 10)
)

DB_MAX_RETRIES = int(
    os.getenv("DB_MAX_RETRIES", 3)
)

# ------------------------------------------------------------------
# VALIDATE CONFIG
# ------------------------------------------------------------------

required_vars = {
    "DB_USERNAME": DB_USERNAME,
    "DB_PASSWORD": DB_PASSWORD,
    "DB_DSN": DB_DSN
}

missing = [
    key for key, value in required_vars.items()
    if not value
]

if missing:
    raise ValueError(
        f"Missing required environment variables: "
        f"{', '.join(missing)}"
    )

# ------------------------------------------------------------------
# ORACLE CONNECTION
# ------------------------------------------------------------------


def connect_to_oracle() -> oracledb.Connection:
    """
    Open Oracle connection with retry and exponential backoff.
    """

    last_error = None

    for attempt in range(1, DB_MAX_RETRIES + 1):

        try:

            logger.info(
                "Connecting to Oracle (attempt %d/%d)...",
                attempt,
                DB_MAX_RETRIES
            )

            conn = oracledb.connect(
                user=DB_USERNAME,
                password=DB_PASSWORD,
                dsn=DB_DSN,
                tcp_connect_timeout=DB_CONNECT_TIMEOUT_S,
            )

            conn.ping()

            logger.info("Oracle connection established successfully")

            return conn

        except oracledb.DatabaseError as e:

            last_error = e

            logger.warning(
                "Connection attempt %d/%d failed: %s",
                attempt,
                DB_MAX_RETRIES,
                str(e)
            )

            if attempt < DB_MAX_RETRIES:

                wait_time = 2 ** attempt

                logger.info(
                    "Retrying in %d seconds...",
                    wait_time
                )

                time.sleep(wait_time)

    raise last_error


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------

if __name__ == "__main__":

    try:

        print("\n" + "=" * 80)
        print("ORACLE CONNECTION TEST")
        print("=" * 80)

        print(f"Username : {DB_USERNAME}")
        print(f"DSN      : {DB_DSN}")

        conn = connect_to_oracle()

        print("\n✅ Connection Successful")

        conn.close()

        print("Connection Closed")

    except Exception as e:

        print("\n❌ Connection Failed")
        print(str(e))