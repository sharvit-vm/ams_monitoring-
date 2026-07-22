import hashlib
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


def _verify_signature(payload: bytes, sig_header: str, webhook_secret: str) -> bool:
    if not webhook_secret:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def handle_github_issue_event(
    *,
    payload_bytes: bytes,
    payload: dict,
    headers: dict,
    queue: EventQueue,
    webhook_secret: str,
    knowledge_id_for_repo: Callable[[ErrorEvent], str],
) -> ConnectorResult:
    """
    Lightweight GitHub Issues connector.

    Responsibilities:
    verify GitHub signature -> build SourceEvent -> hand off to normalization layer -> queue.
    """
    sig = _header(headers, "X-Hub-Signature-256")
    if not _verify_signature(payload_bytes, sig, webhook_secret):
        return ConnectorResult(
            status="unauthorized",
            http_status=401,
            body={"detail": "Invalid webhook signature"},
        )

    event_type = _header(headers, "X-GitHub-Event")
    action = payload.get("action", "")
    if event_type != "issues":
        return ConnectorResult(status="ignored", body={"reason": f"event={event_type}"})
    if action != "opened":
        return ConnectorResult(status="ignored", body={"reason": f"action={action}"})

    issue = payload.get("issue", {})
    source_event = SourceEvent(
        source="github_issue",
        event_type=action,
        external_id=str(issue.get("number") or ""),
        headers={str(k): str(v) for k, v in headers.items()},
        raw_payload=payload,
        raw_body=payload_bytes.decode("utf-8", errors="replace"),
    )
    log_intake_snapshot("raw_source_event", source_event.source, source_event)

    error_event = normalise_source_event(source_event)
    if error_event is None:
        log_intake_snapshot(
            "normalised_event_ignored",
            source_event.source,
            {"reason": "no bug label or no traceback found", "external_id": source_event.external_id},
        )
        return ConnectorResult(status="ignored", body={"reason": "no bug label or no traceback found"})
    log_intake_snapshot(
        "normalised_error_event",
        source_event.source,
        normalised_event_log_payload(error_event),
    )

    pushed = queue.push(error_event)
    if not pushed:
        return ConnectorResult(status="deduplicated", body={"fingerprint": error_event.fingerprint})

    print(f"[github] Queued {error_event.id} - {error_event.error_type}: {error_event.message[:80]}")
    return ConnectorResult(
        status="queued",
        body={
            "event_id": error_event.id,
            "fingerprint": error_event.fingerprint,
            "error_type": error_event.error_type,
            "knowledge_id": knowledge_id_for_repo(error_event),
        },
    )
