import hmac
from collections.abc import Callable

from issuelayer.connectors.base import (
    ConnectorResult,
    log_intake_snapshot,
    normalised_event_log_payload,
)
from issuelayer.intake.normalizers.router import normalise_source_event
from issuelayer.intake.queue import EventQueue
from issuelayer.intake.schemas import ErrorEvent
from issuelayer.intake.source_event import SourceEvent


def _header(headers: dict, name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or ""


def _first_text(*values) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def handle_servicenow_event(
    *,
    payload: dict,
    headers: dict,
    queue: EventQueue,
    webhook_token: str,
    knowledge_id_for_repo: Callable[[ErrorEvent], str],
) -> ConnectorResult:
    """
    Lightweight ServiceNow connector.

    Responsibilities:
    verify ServiceNow token -> build SourceEvent -> hand off to normalization layer -> queue.
    """
    if webhook_token:
        token = _first_text(
            _header(headers, "X-ServiceNow-Token"),
            _header(headers, "X-CodeFixer-Token"),
        )
        if not hmac.compare_digest(token, webhook_token):
            return ConnectorResult(
                status="unauthorized",
                http_status=401,
                body={"detail": "Invalid ServiceNow webhook token"},
            )

    incident_number = _first_text(
        payload.get("number"),
        payload.get("incident"),
        payload.get("incident_id"),
        payload.get("sys_id"),
    )
    event_type = _first_text(
        payload.get("event"),
        payload.get("event_type"),
        payload.get("operation"),
        "incident_created",
    )

    source_event = SourceEvent(
        source="servicenow",
        event_type=event_type,
        external_id=incident_number,
        headers={str(k): str(v) for k, v in headers.items()},
        raw_payload={**payload, "source": "servicenow"},
    )
    log_intake_snapshot("raw_source_event", source_event.source, source_event)

    error_event = normalise_source_event(source_event)
    if error_event is None:
        print(f"[servicenow] Received event but no repo mapping found: {incident_number}")
        log_intake_snapshot(
            "normalised_event_ignored",
            source_event.source,
            {"reason": "missing repo mapping", "external_id": source_event.external_id},
        )
        return ConnectorResult(
            status="received_not_queued",
            body={
                "reason": "missing repo mapping",
                "incident": incident_number,
                "hint": (
                    "Set SERVICENOW_DEFAULT_REPO_URL and SERVICENOW_DEFAULT_REPO_FULL_NAME, "
                    "or include repo_url/repo_full_name in the ServiceNow payload."
                ),
            },
        )

    log_intake_snapshot(
        "normalised_error_event",
        source_event.source,
        normalised_event_log_payload(error_event),
    )

    pushed = queue.push(error_event)
    if not pushed:
        return ConnectorResult(
            status="deduplicated",
            body={
                "fingerprint": error_event.fingerprint,
                "incident": incident_number,
            },
        )

    print(f"[servicenow] Queued {error_event.id} from {incident_number} - {error_event.message[:80]}")
    return ConnectorResult(
        status="queued",
        body={
            "event_id": error_event.id,
            "fingerprint": error_event.fingerprint,
            "incident": incident_number,
            "error_type": error_event.error_type,
            "knowledge_id": knowledge_id_for_repo(error_event),
        },
    )
