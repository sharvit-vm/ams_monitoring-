"""Build retrieval queries from the normalized incident contract."""

from __future__ import annotations

from issuelayer.intake.schemas import ErrorEvent


def build_incident_query(event: ErrorEvent) -> str:
    parts = [
        event.error_type,
        event.message,
        event.short_description or "",
        event.description or "",
        event.raw_description or "",
        event.traceback or "",
        event.file_path or "",
        event.function_name or "",
        event.class_name or "",
        " ".join(event.labels or []),
        " ".join(event.components or []),
    ]
    return "\n".join(part.strip() for part in parts if part and str(part).strip())


def has_strong_traceback_location(event: ErrorEvent) -> bool:
    return bool(event.file_path and event.line_number and event.traceback)
