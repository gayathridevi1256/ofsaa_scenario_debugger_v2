"""Central configuration — all settings from environment/.env."""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from dotenv import load_dotenv

# Pipeline modules print emoji/Unicode status markers (e.g. "✅"). On Windows,
# stdout/stderr default to the console's legacy codepage (cp1252/cp437) rather
# than UTF-8, which raises UnicodeEncodeError the moment such a character is
# printed — this never surfaces on the Linux deployment target, only in local
# Windows dev. Force UTF-8 on both streams so pipeline output never crashes a job.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

load_dotenv()

_default_base = "C:/scenario-debugger" if os.name == "nt" else "/opt/scenario-debugger"
APP_BASE_PATH = Path(os.getenv("APP_BASE_PATH", _default_base))

UPLOADS_DIR = APP_BASE_PATH / "uploads"
OUTPUTS_DIR = APP_BASE_PATH / "outputs"
LOGS_DIR = APP_BASE_PATH / "logs"
DB_DIR = APP_BASE_PATH / "db"
DB_PATH = DB_DIR / "scenario_debugger.db"
PDF_REPORTS_DIR = APP_BASE_PATH / "pdf_reports"

SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_BEFORE_DEPLOYING_USE_SECRETS_TOKEN_HEX")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = int(os.getenv("JWT_EXPIRY_MINUTES", "480"))

APP_NAME = os.getenv("APP_NAME", "ScenarioIQ")
APP_VERSION = "2.0.0"
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_SQL_FILE_BYTES = int(os.getenv("MAX_SQL_FILE_BYTES", str(10 * 1024 * 1024)))

PIPELINE_DIR = Path(os.getenv("PIPELINE_DIR", str(Path(__file__).parent / "pipeline")))
PIPELINE_TIMEOUT_SECONDS = int(os.getenv("PIPELINE_TIMEOUT_SECONDS", "300"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# AI recommendation — 2-line suggestion generated from the diagnosed root cause.
# Uses a local Ollama server (https://ollama.com) so no data leaves the box and
# there's no per-call API cost. If Ollama is unreachable, the pipeline still
# completes normally — the recommendation is simply omitted.
AI_RECOMMENDATION_ENABLED = os.getenv("AI_RECOMMENDATION_ENABLED", "true").lower() == "true"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "90"))  # cold model load (~40s) + generation on CPU-only hardware

LDAP_ENABLED = os.getenv("LDAP_ENABLED", "false").lower() == "true"
LDAP_SERVER = os.getenv("LDAP_SERVER", "")
LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "")
LDAP_BIND_DN = os.getenv("LDAP_BIND_DN", "")
LDAP_BIND_PASS = os.getenv("LDAP_BIND_PASSWORD", "")


def create_app_directories():
    dirs = [UPLOADS_DIR, OUTPUTS_DIR, LOGS_DIR, DB_DIR, PDF_REPORTS_DIR]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def setup_logging():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                LOGS_DIR / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            ),
        ],
    )
    logger = logging.getLogger(__name__)
    logger.info("Logging initialised — level: %s", LOG_LEVEL)
    logger.info("App base path: %s", APP_BASE_PATH)
    return logger


def print_config_summary():
    key_status = "*** SET ***" if SECRET_KEY != "CHANGE_ME_BEFORE_DEPLOYING_USE_SECRETS_TOKEN_HEX" else "[!] NOT SET"
    print(f"\n{'=' * 60}\n  {APP_NAME}  v{APP_VERSION}\n{'=' * 60}")
    print(f"  Base path     : {APP_BASE_PATH}\n  Uploads       : {UPLOADS_DIR}\n  Outputs       : {OUTPUTS_DIR}")
    print(f"  Database      : {DB_PATH}\n  API           : http://{API_HOST}:{API_PORT}\n  CORS origins  : {CORS_ORIGINS}")
    print(f"  JWT expiry    : {JWT_EXPIRY_MINUTES} minutes\n  LDAP enabled  : {LDAP_ENABLED}")
    print(f"  Secret key    : {key_status}\n  Log level     : {LOG_LEVEL}\n{'=' * 60}\n")
