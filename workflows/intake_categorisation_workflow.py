from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from categorisation_adapter import categorise_error_event
from issuelayer.connectors.base import log_intake_snapshot, normalised_event_log_payload
from issuelayer.intake.normalizers.router import normalise_source_event
from issuelayer.intake.schemas import ErrorEvent
from issuelayer.intake.source_event import SourceEvent


class GatewayError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class IntakeCategorisationState(TypedDict, total=False):
    source: str
    payload: dict[str, Any]
    payload_bytes: bytes
    headers: dict[str, str]
    source_event: SourceEvent
    error_event: ErrorEvent
    categorisation: dict[str, Any]
    status: str
    response: dict[str, Any]


def _header(headers: dict[str, str], name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or ""


def _first_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _verify_shared_token(headers: dict[str, str], expected: str, *header_names: str) -> None:
    if not expected:
        return
    supplied = _first_text(*(_header(headers, name) for name in header_names))
    if not hmac.compare_digest(supplied, expected):
        raise GatewayError(401, "Invalid webhook token")


def _verify_github_signature(payload_bytes: bytes, sig_header: str, webhook_secret: str) -> None:
    if not webhook_secret:
        return
    if not sig_header or not sig_header.startswith("sha256="):
        raise GatewayError(401, "Invalid webhook signature")
    expected = "sha256=" + hmac.new(
        webhook_secret.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise GatewayError(401, "Invalid webhook signature")


def _parse_servicenow(state: IntakeCategorisationState) -> SourceEvent:
    payload = state["payload"]
    _verify_shared_token(
        state["headers"],
        os.getenv("SERVICENOW_WEBHOOK_TOKEN", ""),
        "X-ServiceNow-Token",
        "X-CodeFixer-Token",
    )
    incident_number = _first_text(
        payload.get("number"),
        payload.get("incident"),
        payload.get("incident_id"),
        payload.get("sys_id"),
    )
    return SourceEvent(
        source="servicenow",
        event_type=_first_text(
            payload.get("event"),
            payload.get("event_type"),
            payload.get("operation"),
            "incident_created",
        ),
        external_id=incident_number,
        headers={str(k): str(v) for k, v in state["headers"].items()},
        raw_payload={**payload, "source": "servicenow"},
    )


def _parse_jira(state: IntakeCategorisationState) -> SourceEvent:
    payload = state["payload"]
    _verify_shared_token(
        state["headers"],
        os.getenv("JIRA_WEBHOOK_TOKEN", ""),
        "X-CodeFixer-Token",
    )
    issue_key = payload.get("issue_key") or payload.get("key") or ""
    return SourceEvent(
        source="jira",
        event_type=str(payload.get("event") or "work_item_created"),
        external_id=str(issue_key),
        headers={str(k): str(v) for k, v in state["headers"].items()},
        raw_payload=payload,
    )


def _parse_github(state: IntakeCategorisationState) -> SourceEvent:
    payload = state["payload"]
    payload_bytes = state.get("payload_bytes") or b""
    headers = state["headers"]
    _verify_github_signature(
        payload_bytes,
        _header(headers, "X-Hub-Signature-256"),
        os.getenv("GITHUB_WEBHOOK_SECRET", ""),
    )
    event_type = _header(headers, "X-GitHub-Event")
    action = payload.get("action", "")
    if event_type != "issues":
        raise GatewayError(200, f"Ignored GitHub event: event={event_type}")
    if action != "opened":
        raise GatewayError(200, f"Ignored GitHub issue action: action={action}")
    issue = payload.get("issue", {})
    return SourceEvent(
        source="github_issue",
        event_type=action,
        external_id=str(issue.get("number") or ""),
        headers={str(k): str(v) for k, v in headers.items()},
        raw_payload=payload,
        raw_body=payload_bytes.decode("utf-8", errors="replace"),
    )


CONNECTOR_REGISTRY: dict[str, Callable[[IntakeCategorisationState], SourceEvent]] = {
    "servicenow": _parse_servicenow,
    "jira": _parse_jira,
    "github": _parse_github,
}


def supported_sources() -> list[str]:
    return sorted(CONNECTOR_REGISTRY)


def build_intake_categorisation_workflow():
    graph = StateGraph(IntakeCategorisationState)

    def connector_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source = state["source"].lower().strip()
        parser = CONNECTOR_REGISTRY[source]
        print(f"[graph] Connector started; source={source}")
        source_event = parser(state)
        log_intake_snapshot("raw_source_event", source_event.source, source_event)
        print(
            "[graph] Connector completed; "
            f"source={source_event.source}, external_id={source_event.external_id}, "
            f"source_event_id={source_event.id}"
        )
        return {**state, "source_event": source_event, "status": "source_event_created"}

    def normalizer_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source_event = state["source_event"]
        print(
            "[graph] Normalizer started; "
            f"source={source_event.source}, source_event_id={source_event.id}"
        )
        error_event = normalise_source_event(source_event)
        if error_event is None:
            print(f"[graph] Normalizer ignored event; source_event_id={source_event.id}")
            return {
                **state,
                "status": "normalised_event_ignored",
                "response": {
                    "status": "ignored",
                    "reason": "normalizer returned no ErrorEvent",
                    "source_event_id": source_event.id,
                },
            }
        log_intake_snapshot(
            "normalised_error_event",
            source_event.source,
            normalised_event_log_payload(error_event),
        )
        print(
            "[graph] Normalizer completed; "
            f"event={error_event.id}, source={error_event.source}, "
            f"incident={error_event.incident_id or error_event.external_id}, "
            f"message={error_event.message[:100]}"
        )
        return {**state, "error_event": error_event, "status": "normalised"}

    def categorisation_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        print(
            "[graph] Categorisation started; "
            f"event={event.id}, source={event.source}, incident={event.incident_id or event.external_id}"
        )
        result_payload = categorise_error_event(event)
        print(
            "[graph] Categorisation completed; "
            f"event={event.id}, level={result_payload.get('rca_level')}, "
            f"category={result_payload.get('category')}, confidence={result_payload.get('confidence')}, "
            f"human_review={result_payload.get('needs_human_review')}, "
            f"action={result_payload.get('recommended_next_action')}"
        )
        return {
            **state,
            "categorisation": result_payload,
            "status": "categorised",
            "response": {
                "status": "categorised",
                "source_event_id": state["source_event"].id,
                "event_id": event.id,
                "normalised_event": normalised_event_log_payload(event),
                "categorisation": result_payload,
            },
        }

    def route_after_normalizer(state: IntakeCategorisationState) -> str:
        return "categorisation" if state.get("error_event") else "end"

    graph.add_node("connector", connector_node)
    graph.add_node("normalizer", normalizer_node)
    graph.add_node("categorisation", categorisation_node)

    graph.set_entry_point("connector")
    graph.add_edge("connector", "normalizer")
    graph.add_conditional_edges(
        "normalizer",
        route_after_normalizer,
        {
            "categorisation": "categorisation",
            "end": END,
        },
    )
    graph.add_edge("categorisation", END)
    return graph.compile()


_WORKFLOW = None


def run_intake_categorisation_workflow(
    *,
    source: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    payload_bytes: bytes = b"",
) -> IntakeCategorisationState:
    global _WORKFLOW
    if _WORKFLOW is None:
        _WORKFLOW = build_intake_categorisation_workflow()
    return _WORKFLOW.invoke({
        "source": source.lower().strip(),
        "payload": payload,
        "payload_bytes": payload_bytes,
        "headers": headers,
    })
