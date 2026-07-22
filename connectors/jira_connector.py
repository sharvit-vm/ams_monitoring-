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


def handle_jira_event(
    *,
    payload: dict,
    headers: dict,
    queue: EventQueue,
    webhook_token: str,
    knowledge_id_for_repo: Callable[[ErrorEvent], str],
) -> ConnectorResult:
    """
    Lightweight Jira connector.

    Responsibilities:
    verify Jira token -> build SourceEvent -> hand off to normalization layer -> queue.
    """
    if webhook_token:
        token = _header(headers, "X-CodeFixer-Token")
        if not hmac.compare_digest(token, webhook_token):
            return ConnectorResult(
                status="unauthorized",
                http_status=401,
                body={"detail": "Invalid Jira webhook token"},
            )

    issue_key = payload.get("issue_key") or payload.get("key") or ""
    source_event = SourceEvent(
        source="jira",
        event_type=str(payload.get("event") or "work_item_created"),
        external_id=str(issue_key),
        headers={str(k): str(v) for k, v in headers.items()},
        raw_payload=payload,
    )
    log_intake_snapshot("raw_source_event", source_event.source, source_event)

    error_event = normalise_source_event(source_event)
    if error_event is None:
        print(f"[jira] Received event but no repo mapping found: {issue_key}")
        log_intake_snapshot(
            "normalised_event_ignored",
            source_event.source,
            {"reason": "missing repo mapping", "external_id": source_event.external_id},
        )
        return ConnectorResult(
            status="received_not_queued",
            body={
                "reason": "missing repo mapping",
                "jira_issue": issue_key,
                "hint": (
                    "Set JIRA_DEFAULT_REPO_URL and JIRA_DEFAULT_REPO_FULL_NAME, "
                    "or include repo_url/repo_full_name in the Jira payload."
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
                "jira_issue": issue_key,
            },
        )

    print(f"[jira] Queued {error_event.id} from {issue_key} - {error_event.message[:80]}")
    return ConnectorResult(
        status="queued",
        body={
            "event_id": error_event.id,
            "fingerprint": error_event.fingerprint,
            "jira_issue": issue_key,
            "error_type": error_event.error_type,
            "knowledge_id": knowledge_id_for_repo(error_event),
        },
    )
