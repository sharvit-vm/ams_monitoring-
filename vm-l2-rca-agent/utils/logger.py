from telemetry.logger import tlog


def log(message: str, *, ticket_id: str = "", correlation_id: str = "",
        stage: str = "", elapsed_ms: float = None, extra: dict = None,
        level: str = "info"):

    """Backward-compatible wrapper — delegates to telemetry tlog()."""

    _extra = extra or {}
    if ticket_id:
        _extra["ticket_id"] = ticket_id
    if correlation_id:
        _extra["correlation_id"] = correlation_id
    if elapsed_ms is not None:
        _extra["elapsed_ms"] = round(elapsed_ms, 3)

    tlog(
        message,
        event=stage,
        status="INFO",
        extra=_extra if _extra else None,
        level=level
    )
