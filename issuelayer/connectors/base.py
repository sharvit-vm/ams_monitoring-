import json
import logging
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
TRUE_VALUES = {"1", "true", "yes", "on"}

INTAKE_LOGGER = logging.getLogger("ams.intake")
GRAPH_LOGGER = logging.getLogger("ams.graph")


@dataclass
class ConnectorResult:
    status: str
    http_status: int = 200
    body: dict[str, Any] = field(default_factory=dict)

    def as_response_body(self) -> dict[str, Any]:
        return {"status": self.status, **self.body}


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    INTAKE_LOGGER.setLevel(level)
    GRAPH_LOGGER.setLevel(level)
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


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


def _compact(value: Any, max_len: int = 220) -> Any:
    if isinstance(value, str):
        text = " ".join(value.split())
        if len(text) > max_len:
            return text[:max_len] + "...<truncated>"
        return text
    if isinstance(value, list):
        return [_compact(item, max_len) for item in value[:10]]
    if isinstance(value, dict):
        return {str(k): _compact(v, max_len) for k, v in value.items()}
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


def _payload_summary(payload: Any) -> dict[str, Any]:
    data = _redact(_to_dict(payload))
    if not isinstance(data, dict):
        return {"value": _compact(data)}

    summary_keys = [
        "id",
        "source",
        "event_type",
        "external_id",
        "external_url",
        "incident_id",
        "fingerprint",
        "error_type",
        "message",
        "file_path",
        "function_name",
        "line_number",
        "repo_full_name",
        "branch",
        "priority",
        "status",
    ]
    summary = {key: _compact(data.get(key)) for key in summary_keys if data.get(key) not in (None, "", [], {})}

    raw_payload = data.get("raw_payload")
    if isinstance(raw_payload, dict):
        issue = raw_payload.get("issue") if isinstance(raw_payload.get("issue"), dict) else {}
        repository = raw_payload.get("repository") if isinstance(raw_payload.get("repository"), dict) else {}
        summary.update(
            remove_empty_fields(
                {
                    "raw_action": raw_payload.get("action") or raw_payload.get("event"),
                    "raw_issue_key": raw_payload.get("issue_key"),
                    "raw_incident": raw_payload.get("incident"),
                    "raw_issue_number": issue.get("number"),
                    "raw_title": _compact(issue.get("title") or raw_payload.get("summary")),
                    "raw_repo_full_name": repository.get("full_name") or raw_payload.get("repo_full_name"),
                    "raw_payload_keys": sorted(str(key) for key in raw_payload.keys())[:20],
                }
            )
        )

    return remove_empty_fields(summary)


def _emit_json(logger: logging.Logger, prefix: str, data: dict[str, Any]) -> None:
    _configure_logging()
    text = json.dumps(remove_empty_fields(_redact(data)), default=str, ensure_ascii=True)
    max_chars = int(os.getenv("INTAKE_LOG_MAX_CHARS", "12000"))
    if len(text) > max_chars:
        text = text[:max_chars] + "...<truncated>"
    logger.info("%s %s", prefix, text)


def log_graph_stage(stage_order: int, stage: str, status: str = "completed", **fields: Any) -> None:
    _emit_json(
        GRAPH_LOGGER,
        "[graph]",
        {
            "stage_order": f"{stage_order:02d}",
            "stage": stage,
            "status": status,
            **fields,
        },
    )


def log_intake_snapshot(stage: str, source: str, payload: Any, *, stage_order: int | None = None) -> None:
    """
    Emit one ordered, searchable Render log line for intake debugging.

    Set INTAKE_LOG_PAYLOADS=false to disable intake payload logs.
    Set INTAKE_LOG_FULL_PAYLOADS=true to print the full redacted payload.
    Set INTAKE_LOG_MAX_CHARS to control truncation length.
    """
    if os.getenv("INTAKE_LOG_PAYLOADS", "true").lower() not in TRUE_VALUES:
        return

    data: dict[str, Any] = {
        "stage": stage,
        "source": source,
        "payload_summary": _payload_summary(payload),
    }
    if stage_order is not None:
        data["stage_order"] = f"{stage_order:02d}"
    if os.getenv("INTAKE_LOG_FULL_PAYLOADS", "false").lower() in TRUE_VALUES:
        data["payload"] = _redact(_to_dict(payload))
    else:
        data["payload"] = "<full payload omitted; set INTAKE_LOG_FULL_PAYLOADS=true to include>"

    _emit_json(INTAKE_LOGGER, "[intake]", data)
