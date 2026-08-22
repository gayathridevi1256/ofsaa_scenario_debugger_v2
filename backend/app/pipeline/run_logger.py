"""
run_logger.py — Structured step-by-step logging for the OFSAA Scenario Debugger pipeline.

Creates a per-run log file at:
    outputs/<scenario>/run_<scenario>_<job_id_short>.log

Every log line is prefixed with:
    [STEP N] YYYY-MM-DD HH:MM:SS | <message>

SQL statements are logged in full before execution so analysts can
copy/paste them directly into Oracle SQL Developer.
"""

import os
import json
import textwrap
from datetime import datetime


class RunLogger:
    """Structured logger for a single pipeline run."""

    def __init__(self, output_dir: str, job_id: str, scenario_name: str):
        self.output_dir = output_dir
        self.job_id = job_id
        self.scenario_name = scenario_name
        self._short_id = job_id.replace("-", "")[:12] if job_id else "unknown"

        os.makedirs(output_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in scenario_name)
        self.log_path = os.path.join(output_dir, f"run_{safe_name}_{self._short_id}.log")

        # Truncate on new run
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("=" * 100 + "\n")
            f.write(f"  SCENARIO DEBUGGER RUN LOG\n")
            f.write(f"  Scenario : {scenario_name}\n")
            f.write(f"  Job ID   : {job_id}\n")
            f.write(f"  Started  : {self._ts()}\n")
            f.write("=" * 100 + "\n\n")

    # ------------------------------------------------------------------
    # TIMESTAMP
    # ------------------------------------------------------------------

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def log(self, step: int, message: str):
        """Append a plain text message prefixed with [STEP N]."""
        line = f"[STEP {step}] {self._ts()} | {message}\n"
        self._append(line)
        print(line.rstrip())

    def log_sql(self, step: int, label: str, sql: str):
        """Log the full SQL that is about to be executed."""
        header = f"[STEP {step}] {self._ts()} | EXECUTING SQL: {label}"
        separator = "-" * 100
        block = f"{header}\n{separator}\n{sql}\n{separator}\n"
        self._append(block)
        print(f"\n  [STEP {step}] SQL ({label}) — see log for full text")

    def log_block(self, step: int, title: str, data: dict):
        """Log a structured dict as pretty-printed JSON."""
        header = f"[STEP {step}] {self._ts()} | {title}"
        body = json.dumps(data, indent=2, default=str)
        block = f"{header}\n{body}\n"
        self._append(block)
        print(f"\n  [STEP {step}] {title}")

    def log_json(self, step: int, label: str, data: dict):
        """Log a compact JSON line."""
        line = f"[STEP {step}] {self._ts()} | {label}: {json.dumps(data, default=str)}\n"
        self._append(line)

    def section(self, title: str):
        """Print a visual section separator."""
        block = f"\n{'=' * 100}\n  {title}\n{'=' * 100}\n"
        self._append(block)
        print(block)

    def summary(self, step: int, title: str, summary_lines: list[str]):
        """Log a multi-line summary block."""
        header = f"[STEP {step}] {self._ts()} | {title}"
        body = "\n".join(f"  {l}" for l in summary_lines)
        block = f"{header}\n{body}\n"
        self._append(block)
        print(f"\n  [STEP {step}] {title}")
        for l in summary_lines:
            print(f"    {l}")

    def close(self):
        """Append footer."""
        footer = f"\n{'=' * 100}\n  Run completed: {self._ts()}\n{'=' * 100}\n"
        self._append(footer)

    # ------------------------------------------------------------------
    # INTERNAL
    # ------------------------------------------------------------------

    def _append(self, text: str):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text)
