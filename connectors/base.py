import json
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-codefixer-token",
    "x-hub-signature-256",
    "x-servicenow-token",
    "token",
    "secret",
    "password",
}
EMPTY_NUMERIC_FIELDS = {"line_number"}


@dataclass
class ConnectorResult:
    status: str
    http_status: int = 200
    body: dict[str, Any] = field(default_factory=dict)

    def as_response_body(self) -> dict[str, Any]:
        return {"status": self.status, **self.body}


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _to_dict(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def remove_empty_fields(value: Any, key: str = "") -> Any:
    """
    Recursively remove empty display fields from payloads.

    This is only for logs/API display payloads. The durable ErrorEvent still
    keeps the full schema so downstream code has a stable contract.
    """
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            cleaned_item = remove_empty_fields(item, key)
            if cleaned_item in (None, "", [], {}):
                continue
            cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [
            cleaned_item
            for item in value
            if (cleaned_item := remove_empty_fields(item, key)) not in (None, "", [], {})
        ]
    if key in EMPTY_NUMERIC_FIELDS and value == 0:
        return None
    return value


def normalised_event_log_payload(event: BaseModel) -> dict[str, Any]:
    payload = event.model_dump(mode="json")
    payload["raw_payload"] = "<preserved on ErrorEvent; see raw_source_event log>"
    return remove_empty_fields(payload)


def log_intake_snapshot(stage: str, source: str, payload: Any) -> None:
    """
    Emit one searchable Render log line for intake debugging.

    Set INTAKE_LOG_PAYLOADS=false to disable payload logs.
    Set INTAKE_LOG_MAX_CHARS to control truncation length.
    """
    if os.getenv("INTAKE_LOG_PAYLOADS", "true").lower() not in ("1", "true", "yes"):
        return

    data = {
        "stage": stage,
        "source": source,
        "payload": _redact(_to_dict(payload)),
    }
    text = json.dumps(data, default=str, ensure_ascii=False)
    max_chars = int(os.getenv("INTAKE_LOG_MAX_CHARS", "12000"))
    if len(text) > max_chars:
        text = text[:max_chars] + "...<truncated>"
    print(f"[intake] {text}")
