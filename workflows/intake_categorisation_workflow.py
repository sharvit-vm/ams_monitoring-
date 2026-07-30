from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from categorisation_adapter import categorise_error_event
from issuelayer.connectors.base import log_intake_snapshot, normalised_event_log_payload
from issuelayer.intake.normalizers.router import normalise_source_event
from issuelayer.intake.schemas import ErrorEvent
from issuelayer.intake.source_event import SourceEvent

# ── Namespaced imports — no sys.path collision ────────────────────────────────
from l2_rca.agents.l2_rca_agent import L2RCAAgent
from l2_rca.models.request import IncidentRequest as L2IncidentRequest
from db_fix.agents.db_fix_agent import DBFixAgent
from db_fix.models.request import RCARequest as DBFixRequest
from workflows.agent_registry import get_fix_agent


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
    l2_rca_result: dict[str, Any]
    fix_agent_result: dict[str, Any]
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


def _should_route_to_l2(categorisation: dict[str, Any]) -> bool:
    if categorisation.get("reject") or not categorisation.get("is_valid_incident", True):
        return False
    if categorisation.get("needs_human_review") or categorisation.get("requires_human"):
        return False
    support_level = str(
        categorisation.get("support_level") or categorisation.get("rca_level") or ""
    ).upper()
    action = str(categorisation.get("recommended_next_action") or "").lower()
    return support_level == "L2" or action == "run_l2_rca"


def _compact_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, list):
            text = "\n".join(str(item) for item in value if item)
        else:
            text = str(value or "")
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _build_l2_request(event: ErrorEvent, categorisation: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": (
            event.incident_id
            or event.external_id
            or categorisation.get("ticket_id")
            or event.id
        ),
        "classification": str(categorisation.get("classification") or "INCIDENT"),
        "support_level":  str(categorisation.get("support_level") or "L2"),
        "technology":     str(categorisation.get("technology") or categorisation.get("category") or "General"),
        "team":           str(categorisation.get("team") or categorisation.get("technology") or "General"),
        "application":    str(
            event.configuration_item
            or event.assignment_group
            or categorisation.get("team")
            or categorisation.get("technology")
            or "Unknown"
        ),
        "title":       str(event.short_description or event.message or event.error_type or "Incident"),
        "description": _compact_text(
            event.description,
            event.raw_description,
            event.message,
            event.traceback,
            event.comments,
        ),
        "priority":   str(categorisation.get("priority") or event.priority or "P3"),
        "confidence": float(categorisation.get("confidence") or 0.0),
    }


# ── Connector parsers ─────────────────────────────────────────────────────────

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
    payload      = state["payload"]
    payload_bytes = state.get("payload_bytes") or b""
    headers      = state["headers"]
    _verify_github_signature(
        payload_bytes,
        _header(headers, "X-Hub-Signature-256"),
        os.getenv("GITHUB_WEBHOOK_SECRET", ""),
    )
    event_type = _header(headers, "X-GitHub-Event")
    action     = payload.get("action", "")
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
    "jira":       _parse_jira,
    "github":     _parse_github,
}


def supported_sources() -> list[str]:
    return sorted(CONNECTOR_REGISTRY)


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_intake_categorisation_workflow():
    graph = StateGraph(IntakeCategorisationState)

    # ── Node 1: Connector ─────────────────────────────────────────────────────
    def connector_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source = state["source"].lower().strip()
        parser = CONNECTOR_REGISTRY[source]
        print(f"[graph] Connector started; source={source}")
        source_event = parser(state)
        log_intake_snapshot("raw_source_event", source_event.source, source_event)
        print(
            f"[graph] Connector completed; source={source_event.source}, "
            f"external_id={source_event.external_id}, source_event_id={source_event.id}"
        )
        return {**state, "source_event": source_event, "status": "source_event_created"}

    # ── Node 2: Normalizer ────────────────────────────────────────────────────
    def normalizer_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source_event = state["source_event"]
        print(f"[graph] Normalizer started; source={source_event.source}, source_event_id={source_event.id}")
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
            f"[graph] Normalizer completed; event={error_event.id}, "
            f"incident={error_event.incident_id or error_event.external_id}, "
            f"message={error_event.message[:100]}"
        )
        return {**state, "error_event": error_event, "status": "normalised"}

    # ── Node 3: Categorisation ────────────────────────────────────────────────
    def categorisation_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        print(f"[graph] Categorisation started; event={event.id}, incident={event.incident_id or event.external_id}")
        result_payload = categorise_error_event(event)
        print(
            f"[graph] Categorisation completed; event={event.id}, "
            f"level={result_payload.get('rca_level')}, category={result_payload.get('category')}, "
            f"confidence={result_payload.get('confidence')}, "
            f"human_review={result_payload.get('needs_human_review')}, "
            f"action={result_payload.get('recommended_next_action')}"
        )
        return {**state, "categorisation": result_payload, "status": "categorised"}

    # ── Node 4: L2 RCA ────────────────────────────────────────────────────────
    def l2_rca_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event         = state["error_event"]
        categorisation = state["categorisation"]
        l2_request_dict = _build_l2_request(event, categorisation)

        print(f"[graph] L2 RCA node started; ticket={l2_request_dict['ticket_id']}, technology={l2_request_dict['technology']}")

        try:
            agent   = L2RCAAgent()
            request = L2IncidentRequest(**l2_request_dict)
            result  = agent.analyze(request)
            result_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            result_dict["status"] = "completed"
            print(f"[graph] L2 RCA node completed; ticket={l2_request_dict['ticket_id']}, decision={result_dict.get('decision')}")
        except Exception as exc:
            result_dict = {"status": "failed", "error": str(exc), "ticket_id": l2_request_dict["ticket_id"]}
            print(f"[graph] L2 RCA node failed; error={exc}")

        return {**state, "l2_rca_result": result_dict, "status": "l2_rca_completed"}

    # ── Node 5: Fix Agent (dynamic — driven by problem_domain) ───────────────
    def fix_agent_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        l2_result  = state.get("l2_rca_result", {})
        rca_data   = l2_result.get("data", {})
        rca_report = rca_data.get("rca", {})

        problem_domain = (
            rca_report.get("problem_domain")
            if isinstance(rca_report, dict)
            else getattr(rca_report, "problem_domain", None)
        ) or ""

        print(f"[graph] Fix agent node started; problem_domain={problem_domain}")

        fix_agent = get_fix_agent(problem_domain)

        if fix_agent is None:
            result_dict = {
                "status": "skipped",
                "reason": f"No fix agent registered for problem_domain='{problem_domain}'",
                "problem_domain": problem_domain,
            }
            print(f"[graph] Fix agent node skipped; {result_dict['reason']}")
        else:
            rca_dict = rca_report if isinstance(rca_report, dict) else (
                rca_report.model_dump() if hasattr(rca_report, "model_dump") else {}
            )
            try:
                fix_request = DBFixRequest(**rca_dict)
                result_dict = fix_agent(fix_request)
                if hasattr(result_dict, "model_dump"):
                    result_dict = result_dict.model_dump()
                result_dict["status"] = result_dict.get("status", "completed")
                print(f"[graph] Fix agent node completed; problem_domain={problem_domain}, status={result_dict.get('status')}")
            except Exception as exc:
                result_dict = {"status": "failed", "problem_domain": problem_domain, "error": str(exc)}
                print(f"[graph] Fix agent node failed; error={exc}")

        return {**state, "fix_agent_result": result_dict, "status": "fix_agent_completed"}

    # ── Node 6: Build response ────────────────────────────────────────────────
    def build_response_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event        = state.get("error_event")
        source_event = state.get("source_event")
        response = {
            "status":          state.get("status", "completed"),
            "source_event_id": source_event.id if source_event else None,
            "event_id":        event.id if event else None,
            "normalised_event": normalised_event_log_payload(event) if event else None,
            "categorisation":  state.get("categorisation"),
            "l2_rca":          state.get("l2_rca_result"),
            "fix_agent":       state.get("fix_agent_result"),
        }
        return {**state, "response": response}

    # ── Routing ───────────────────────────────────────────────────────────────
    def route_after_normalizer(state: IntakeCategorisationState) -> str:
        return "categorisation" if state.get("error_event") else "build_response"

    def route_after_categorisation(state: IntakeCategorisationState) -> str:
        return "l2_rca" if _should_route_to_l2(state.get("categorisation", {})) else "build_response"

    def route_after_l2_rca(state: IntakeCategorisationState) -> str:
        l2_result = state.get("l2_rca_result", {})
        if l2_result.get("status") != "completed":
            return "build_response"
        rca_report = l2_result.get("data", {}).get("rca", {})
        problem_domain = (
            rca_report.get("problem_domain")
            if isinstance(rca_report, dict)
            else getattr(rca_report, "problem_domain", None)
        ) or ""
        return "fix_agent" if get_fix_agent(problem_domain) is not None else "build_response"

    # ── Register nodes and edges ──────────────────────────────────────────────
    graph.add_node("connector",      connector_node)
    graph.add_node("normalizer",     normalizer_node)
    graph.add_node("categorisation", categorisation_node)
    graph.add_node("l2_rca",         l2_rca_node)
    graph.add_node("fix_agent",      fix_agent_node)
    graph.add_node("build_response", build_response_node)

    graph.set_entry_point("connector")
    graph.add_edge("connector", "normalizer")
    graph.add_conditional_edges(
        "normalizer", route_after_normalizer,
        {"categorisation": "categorisation", "build_response": "build_response"},
    )
    graph.add_conditional_edges(
        "categorisation", route_after_categorisation,
        {"l2_rca": "l2_rca", "build_response": "build_response"},
    )
    graph.add_conditional_edges(
        "l2_rca", route_after_l2_rca,
        {"fix_agent": "fix_agent", "build_response": "build_response"},
    )
    graph.add_edge("fix_agent",      "build_response")
    graph.add_edge("build_response", END)

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
        "source":        source.lower().strip(),
        "payload":       payload,
        "payload_bytes": payload_bytes,
        "headers":       headers,
    })
