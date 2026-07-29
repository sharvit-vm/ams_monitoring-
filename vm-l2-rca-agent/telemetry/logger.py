import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional


class TelemetryFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("trace_id", "ticket_id", "agent", "event", "status",
                      "decision", "confidence", "latency_ms", "model", "extra"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        return json.dumps(entry)


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(TelemetryFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


_logger = _build_logger("L2-RCA")


def tlog(
    message: str,
    *,
    event: str = "",
    status: str = "",
    decision: str = "",
    confidence: Optional[float] = None,
    latency_ms: Optional[float] = None,
    model: str = "",
    extra: Optional[dict] = None,
    level: str = "info"
):
    from telemetry.tracer import get_trace_id

    fields = {"trace_id": get_trace_id()}
    if event:       fields["event"] = event
    if status:      fields["status"] = status
    if decision:    fields["decision"] = decision
    if confidence is not None: fields["confidence"] = confidence
    if latency_ms is not None: fields["latency_ms"] = round(latency_ms, 3)
    if model:       fields["model"] = model
    if extra:       fields["extra"] = extra

    record = logging.LogRecord(
        name="L2-RCA", level=getattr(logging, level.upper(), logging.INFO),
        pathname="", lineno=0, msg=message, args=(), exc_info=None
    )
    for k, v in fields.items():
        setattr(record, k, v)

    _logger.handle(record)
