from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import asyncio
from threading import Lock
from typing import Any
from uuid import uuid4
from contextvars import ContextVar
from storage.job_store import job_store


_LOCK = Lock()
_LATEST_EXECUTION: dict[str, Any] | None = None
_SUBSCRIBERS: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
execution_id = ContextVar("execution_id", default=None)


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
    identity = execution_id.get() or response.get("workflow_session_id") or str(uuid4())
    execution = {
        "id": identity,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "response": _json_safe_value(response),
    }
    job_store.save_snapshot(identity, execution)
    with _LOCK:
        global _LATEST_EXECUTION
        _LATEST_EXECUTION = execution
        subscribers = list(_SUBSCRIBERS.items())
    for subscriber, loop in subscribers:
        try:
            loop.call_soon_threadsafe(_publish, subscriber, execution)
        except RuntimeError:
            unsubscribe_executions(subscriber)
    return execution


def latest_execution() -> dict[str, Any] | None:
    return job_store.snapshot()


def execution_for_session(session_id: str) -> dict[str, Any] | None:
    return job_store.snapshot(session_id) if session_id else None


def _publish(queue: asyncio.Queue, execution: dict[str, Any]) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(execution)


def subscribe_executions() -> asyncio.Queue:
    queue = asyncio.Queue(maxsize=100)
    with _LOCK:
        _SUBSCRIBERS[queue] = asyncio.get_running_loop()
    return queue


def unsubscribe_executions(queue: asyncio.Queue) -> None:
    with _LOCK:
        _SUBSCRIBERS.pop(queue, None)
