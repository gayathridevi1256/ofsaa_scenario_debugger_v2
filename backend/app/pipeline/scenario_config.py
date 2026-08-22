# ----------------------------------------------------------------------
# SCENARIO CONFIGURATION
# ----------------------------------------------------------------------

import os

# Maximum allowed SQL file size (default 10MB)
MAX_SQL_FILE_BYTES = int(os.getenv("MAX_SQL_FILE_BYTES", str(10 * 1024 * 1024)))

# Scenario IDs that use Oracle @MINER@ functions (require multi-query extraction)
# Source: scenario_categorization.md - Category 1 (Functions) + Category 2 (Pipeline)
FUNCTION_SCENARIO_IDS = {
    # Category 1: @MINER@.F_* Functions (12 scenarios)
    "117350046",  # FR-ChkMISequentialNumber
    "117350005",  # FR-Kiting
    "114000065",  # ML-ChkMISequentialNumber
    "114000071",  # ML-ChkMISequentialNumber
    "118860034",  # ML-ChkMISequentialNumber
    "118860035",  # ML-ChkMISequentialNumber
    "118725006",  # ML-PotStructuringCashAndEquiv
    "118860031",  # ML-PotStructuringCashAndEquiv
    "116000046",  # ML-StructuringAvoidReportThreshold
    "118860028",  # ML-StructuringAvoidReportThreshold
    "118860029",  # ML-StructuringAvoidReportThreshold
    "118860030",  # ML-StructuringAvoidReportThreshold
    # Category 2: @MINER@.ANOMATMEXCESS_PIPELINE (1 scenario)
    "116000065",  # ML-AnomATMBCExcessiveWD
}
