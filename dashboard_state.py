from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


_LOCK = Lock()
_LATEST_EXECUTION: dict[str, Any] | None = None

def record_execution(response: dict[str, Any]) -> dict[str, Any]:
    execution = {
        "id": str(uuid4()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "response": response,
    }
    with _LOCK:
        global _LATEST_EXECUTION
        _LATEST_EXECUTION = execution
    return execution


def latest_execution() -> dict[str, Any] | None:
    with _LOCK:
        return _LATEST_EXECUTION.copy() if _LATEST_EXECUTION else None
