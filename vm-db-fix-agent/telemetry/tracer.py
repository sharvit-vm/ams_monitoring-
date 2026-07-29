"""
telemetry/tracer.py
────────────────────────────────────────────────────────────────────────────
Trace context for every pipeline execution.

Generates and carries:
  trace_id       — unique per agent invocation (this service's root span)
  correlation_id — propagated from the upstream L2 RCA Agent
  request_id     — HTTP request level ID injected by middleware
  ticket_id      — ITSM ticket being processed

These IDs appear in every structured JSON event, every human log line,
and in the final API response so downstream consumers can correlate.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional


class TraceContext:
    """Immutable trace context carried through the entire pipeline."""

    __slots__ = (
        "trace_id",
        "ticket_id",
        "correlation_id",
        "request_id",
        "started_at",
        "started_ts",
    )

    def __init__(
        self,
        ticket_id: str,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        self.trace_id       = str(uuid.uuid4())
        self.ticket_id      = ticket_id
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.request_id     = request_id     or str(uuid.uuid4())
        self.started_at     = datetime.now(timezone.utc)
        self.started_ts     = self.started_at.isoformat()

    def to_dict(self) -> dict:
        return {
            "trace_id":       self.trace_id,
            "ticket_id":      self.ticket_id,
            "correlation_id": self.correlation_id,
            "request_id":     self.request_id,
            "started_at":     self.started_ts,
        }

    def elapsed_ms(self) -> float:
        delta = datetime.now(timezone.utc) - self.started_at
        return round(delta.total_seconds() * 1000, 2)


def new_trace(
    ticket_id: str,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TraceContext:
    """Factory — create a fresh TraceContext for a pipeline run."""
    return TraceContext(
        ticket_id=ticket_id,
        correlation_id=correlation_id,
        request_id=request_id,
    )
