"""
Human-readable execution logger.

Produces formatted plain-text output for developer visibility.
Does NOT replace the JSON telemetry logger — both run in parallel.

Usage:
    from telemetry.execution_logger import ExecutionLogger
    xlog = ExecutionLogger("L2 RCA Agent")
    xlog.banner("Analyzing Incident INC001")
    xlog.step(1, "INCIDENT RECEIVED", "Validate and register incoming incident")
    xlog.substep("CONNECTING", "Reading incident payload")
    xlog.substep("COMPLETED", "Incident registered successfully")
    xlog.summary({...})
"""

import sys
from datetime import datetime, timezone
from typing import Optional

_W = 72  # total banner width


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def _print(line: str = "") -> None:
    print(line, flush=True, file=sys.stdout)


class ExecutionLogger:

    def __init__(self, agent_name: str = "Agent"):
        self.agent_name = agent_name

    # ── Public helpers ────────────────────────────────────────────────────────

    def banner(self, title: str) -> None:
        """Top-level pipeline banner."""
        _print()
        _print("╔" + "═" * (_W - 2) + "╗")
        _print("║" + f"  {self.agent_name}  —  {title}".center(_W - 2) + "║")
        _print("║" + f"  Started: {_now()}".ljust(_W - 2) + "║")
        _print("╚" + "═" * (_W - 2) + "╝")

    def step(self, number: int, title: str, purpose: str) -> None:
        """Begin a major processing stage."""
        _print()
        _print("┌" + "─" * (_W - 2) + "┐")
        _print("│" + f"  STEP {number}  ·  {title}".ljust(_W - 2) + "│")
        _print("│" + f"  Purpose : {purpose}".ljust(_W - 2) + "│")
        _print("│" + f"  Time    : {_now()}".ljust(_W - 2) + "│")
        _print("└" + "─" * (_W - 2) + "┘")

    def substep(self, event: str, detail: str, elapsed_ms: Optional[float] = None) -> None:
        """A sub-event within a step."""
        tag = f"[{event}]".ljust(14)
        timing = f"  ({round(elapsed_ms, 1)} ms)" if elapsed_ms is not None else ""
        _print(f"    {tag}  {detail}{timing}")

    def divider(self) -> None:
        _print("    " + "·" * (_W - 4))

    def summary(self, data: dict) -> None:
        """Final pipeline summary block."""
        _print()
        _print("╔" + "═" * (_W - 2) + "╗")
        _print("║" + "  PIPELINE SUMMARY".center(_W - 2) + "║")
        _print("╠" + "═" * (_W - 2) + "╣")
        for key, value in data.items():
            line = f"  {str(key).ljust(22)}  {value}"
            _print("║" + line.ljust(_W - 2) + "║")
        _print("╚" + "═" * (_W - 2) + "╝")
        _print()
