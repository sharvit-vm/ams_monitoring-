from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from queue import Queue
from threading import Lock
from typing import Any
from uuid import uuid4


_LOCK = Lock()
_LATEST_EXECUTION: dict[str, Any] | None = None
_SUBSCRIBERS: set[Queue] = set()


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def record_execution(response: dict[str, Any]) -> dict[str, Any]:
    execution = {
        "id": str(uuid4()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "response": _json_safe_value(response),
    }
    subscribers: list[Queue]
    with _LOCK:
        global _LATEST_EXECUTION
        _LATEST_EXECUTION = execution
        subscribers = list(_SUBSCRIBERS)
    for subscriber in subscribers:
        subscriber.put(execution)
    return execution


def latest_execution() -> dict[str, Any] | None:
    with _LOCK:
        return _LATEST_EXECUTION.copy() if _LATEST_EXECUTION else None


def subscribe_executions() -> Queue:
    queue: Queue = Queue()
    with _LOCK:
        _SUBSCRIBERS.add(queue)
    return queue


def unsubscribe_executions(queue: Queue) -> None:
    with _LOCK:
        _SUBSCRIBERS.discard(queue)
