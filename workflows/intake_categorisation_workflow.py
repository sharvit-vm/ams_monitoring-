from __future__ import annotations

import hashlib
import hmac
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, TypedDict
from urllib.parse import urlparse, urlunparse

from langgraph.graph import END, StateGraph

from agents.code_fix import run_code_fix
from agents.l3_rca import run_l3_rca
from categorisation_adapter import categorise_error_event
from issuelayer.connectors.base import log_graph_stage, log_intake_snapshot, normalised_event_log_payload
from issuelayer.intake.normalizers.router import normalise_source_event
from issuelayer.intake.schemas import ErrorEvent
from issuelayer.intake.source_event import SourceEvent
from storage.rca_report_store import save_rca_report

from l2_rca.agents.l2_rca_agent import L2RCAAgent
from l2_rca.models.request import IncidentRequest as L2IncidentRequest
from db_fix.models.request import RCARequest as DBFixRequest
from workflows.agent_registry import get_fix_agent


CLONE_ROOT = os.getenv("CLONE_ROOT", "clone")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
AUTO_INGEST_ON_WEBHOOK = os.getenv("AUTO_INGEST_ON_WEBHOOK", "true").lower() == "true"
AUTO_VECTOR_INGEST_ON_WEBHOOK = os.getenv("AUTO_VECTOR_INGEST_ON_WEBHOOK", "false").lower() == "true"


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
    knowledge_id: str
    repo_dir: str
    l1_result: dict[str, Any]
    l2_rca_result: dict[str, Any]
    fix_agent_result: dict[str, Any]
    l3_rca_result: Any
    rca_report_path: str
    codefix_result: Any
    status: str
    reason: str
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


def _support_level(categorisation: dict[str, Any]) -> str:
    return str(
        categorisation.get("support_level") or categorisation.get("rca_level") or ""
    ).upper()


def _is_rejected_or_review(categorisation: dict[str, Any]) -> bool:
    return (
        bool(categorisation.get("reject"))
        or not categorisation.get("is_valid_incident", True)
        or bool(categorisation.get("needs_human_review"))
        or bool(categorisation.get("requires_human"))
    )


def _should_route_to_l2(categorisation: dict[str, Any]) -> bool:
    if _is_rejected_or_review(categorisation):
        return False
    action = str(categorisation.get("recommended_next_action") or "").lower()
    return _support_level(categorisation) == "L2" or action == "run_l2_rca"


def _should_route_to_l3(categorisation: dict[str, Any]) -> bool:
    if _is_rejected_or_review(categorisation):
        return False
    action = str(categorisation.get("recommended_next_action") or "").lower()
    return _support_level(categorisation) == "L3" or action in {"run_l3_rca", "run_codefix"}


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
        "ticket_id": event.incident_id or event.external_id or categorisation.get("ticket_id") or event.id,
        "classification": str(categorisation.get("classification") or "INCIDENT"),
        "support_level": str(categorisation.get("support_level") or "L2"),
        "technology": str(categorisation.get("technology") or categorisation.get("category") or "General"),
        "team": str(categorisation.get("team") or categorisation.get("technology") or "General"),
        "application": str(
            event.configuration_item
            or getattr(event, "business_application", None)
            or getattr(event, "cmdb_ci", None)
            or event.assignment_group
            or categorisation.get("business_application")
            or categorisation.get("application")
            or categorisation.get("team")
            or categorisation.get("technology")
            or "Unknown"
        ),
        "title": str(event.short_description or event.message or event.error_type or "Incident"),
        "description": _compact_text(
            event.description,
            event.raw_description,
            event.message,
            event.traceback,
            event.comments,
        ),
        "priority": str(categorisation.get("priority") or event.priority or "P3"),
        "confidence": float(categorisation.get("confidence") or 0.0),
    }


def _knowledge_id_for_repo(event: ErrorEvent) -> str:
    identity = (event.repo_full_name or event.repo_url).strip().lower()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _repo_dir_for_event(event: ErrorEvent) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "__", event.repo_full_name or event.repo_url)
    return str(Path(CLONE_ROOT) / slug)


def _repo_url_with_token(repo_url: str) -> str:
    if not GITHUB_TOKEN or not repo_url.startswith("https://"):
        return repo_url
    parsed = urlparse(repo_url)
    if "github.com" not in parsed.netloc:
        return repo_url
    netloc = f"x-access-token:{GITHUB_TOKEN}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _git(args: list[str], cwd: str, check: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if GITHUB_TOKEN:
        stdout = stdout.replace(GITHUB_TOKEN, "***")
        stderr = stderr.replace(GITHUB_TOKEN, "***")
    if check and result.returncode != 0:
        raise RuntimeError(stderr or stdout or f"git {' '.join(args)} failed")
    return result.returncode, stdout, stderr


def _ensure_repo_checkout(event: ErrorEvent) -> str:
    repo_dir = _repo_dir_for_event(event)
    branch = event.branch or "main"
    auth_url = _repo_url_with_token(event.repo_url)

    if (Path(repo_dir) / ".git").exists():
        _git(["remote", "set-url", "origin", auth_url], cwd=repo_dir, check=True)
        try:
            _git(["fetch", "origin", branch], cwd=repo_dir, check=True)
            rc, _, _ = _git(["checkout", branch], cwd=repo_dir)
            if rc != 0:
                _git(["checkout", "-b", branch, f"origin/{branch}"], cwd=repo_dir, check=True)
            _git(["pull", "--ff-only", "origin", branch], cwd=repo_dir, check=True)
        finally:
            _git(["remote", "set-url", "origin", event.repo_url], cwd=repo_dir)
        return repo_dir

    Path(CLONE_ROOT).mkdir(parents=True, exist_ok=True)
    rc, _, err = _git(["clone", "--branch", branch, auth_url, repo_dir], cwd=".")
    if rc != 0:
        raise RuntimeError(f"Clone failed for {event.repo_full_name}: {err}")
    _git(["remote", "set-url", "origin", event.repo_url], cwd=repo_dir)
    return repo_dir


def _resolve_event_path(event: ErrorEvent, repo_dir: str) -> None:
    if not event.file_path:
        return

    current = Path(repo_dir) / event.file_path
    if current.exists():
        event.file_path = event.file_path.replace("\\", "/")
        return

    candidates = list(Path(repo_dir).rglob(Path(event.file_path).name))
    if not candidates:
        return

    requested = event.file_path.replace("\\", "/").lstrip("/")
    requested_parts = requested.split("/")
    requested_suffix = "/".join(requested_parts[-min(3, len(requested_parts)):])

    preferred = [
        p
        for p in candidates
        if str(p.relative_to(repo_dir)).replace("\\", "/").endswith(requested_suffix)
    ]

    if not preferred and event.file_path.endswith(".java"):
        class_name = getattr(event, "class_name", "") or Path(event.file_path).stem
        package_path = "/".join(class_name.split(".")[:-1])
        simple_class_name = class_name.split(".")[-1]
        preferred = [
            p
            for p in candidates
            if (
                (package_path and package_path in str(p).replace("\\", "/"))
                or p.stem == simple_class_name
                or str(p).replace("\\", "/").endswith(f"/{simple_class_name}.java")
            )
        ]

    chosen = preferred[0] if preferred else candidates[0]
    resolved = str(chosen.relative_to(repo_dir)).replace("\\", "/")
    print(f"[graph] Resolved event path: {event.file_path} -> {resolved}")
    event.file_path = resolved


def _ingest_repo_for_rca(repo_dir: str, knowledge_id: str) -> None:
    if not AUTO_INGEST_ON_WEBHOOK:
        print("[graph] AUTO_INGEST_ON_WEBHOOK=false; skipping repo ingestion")
        return

    from models import PipelineState
    from phases.file_analysis import analyze_files
    from phases.hierarchy import build_hierarchy
    from phases.llm_analysis import analyze_with_llm
    from phases.neo4j_ingest import neo4j_ingest
    from phases.scanner import scan_repo

    print(f"[graph] Ingesting repo into Neo4j; knowledge_id={knowledge_id}")
    state = PipelineState(repo_path=repo_dir, knowledge_id=knowledge_id)
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)
    state = build_hierarchy(state)
    state = neo4j_ingest(state)

    if AUTO_VECTOR_INGEST_ON_WEBHOOK:
        from phases.vector_ingest import vector_ingest

        print(f"[graph] Ingesting repo into Pinecone; knowledge_id={knowledge_id}")
        state = vector_ingest(state)

    print(f"[graph] Repo ingestion ready; files={len(state.files)}, knowledge_id={knowledge_id}")


# Connector parsers.

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


# Graph builder.

def build_intake_categorisation_workflow():
    graph = StateGraph(IntakeCategorisationState)

    def connector_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source = state["source"].lower().strip()
        parser = CONNECTOR_REGISTRY[source]
        log_graph_stage(1, "connector_started", "running", source=source)
        source_event = parser(state)
        log_intake_snapshot("raw_source_event", source_event.source, source_event, stage_order=2)
        log_graph_stage(
            3,
            "connector_completed",
            "completed",
            source=source_event.source,
            external_id=source_event.external_id,
            source_event_id=source_event.id,
        )
        return {**state, "source_event": source_event, "status": "source_event_created"}

    def normalizer_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        source_event = state["source_event"]
        log_graph_stage(4, "normalizer_started", "running", source=source_event.source, source_event_id=source_event.id)
        error_event = normalise_source_event(source_event)
        if error_event is None:
            log_graph_stage(5, "normalizer_ignored", "completed", source_event_id=source_event.id)
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
            stage_order=5,
        )
        log_graph_stage(
            6,
            "normalizer_completed",
            "completed",
            event=error_event.id,
            incident=error_event.incident_id or error_event.external_id,
            message=error_event.message[:100],
        )
        return {**state, "error_event": error_event, "status": "normalised"}

    def categorisation_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        log_graph_stage(7, "categorisation_started", "running", event=event.id, incident=event.incident_id or event.external_id)
        result_payload = categorise_error_event(event)
        log_graph_stage(
            8,
            "categorisation_completed",
            "completed",
            event=event.id,
            level=result_payload.get("rca_level"),
            category=result_payload.get("category"),
            confidence=result_payload.get("confidence"),
            human_review=result_payload.get("needs_human_review"),
            action=result_payload.get("recommended_next_action"),
        )
        return {**state, "categorisation": result_payload, "status": "categorised"}

    def l1_placeholder_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        result_dict = {
            "status": "skipped",
            "message": "L1 RCA/remediation is not implemented yet.",
            "event_id": event.id,
            "incident_id": event.incident_id or event.external_id,
        }
        print(f"[graph] L1 placeholder completed; event={event.id}")
        return {**state, "l1_result": result_dict, "status": "l1_placeholder_completed"}

    def l2_rca_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        categorisation = state["categorisation"]
        l2_request_dict = _build_l2_request(event, categorisation)
        print(f"[graph] L2 RCA node started; ticket={l2_request_dict['ticket_id']}, technology={l2_request_dict['technology']}")

        try:
            agent = L2RCAAgent()
            request = L2IncidentRequest(**l2_request_dict)
            result = agent.analyze(request)
            result_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            result_dict["status"] = "completed"
            print(f"[graph] L2 RCA node completed; ticket={l2_request_dict['ticket_id']}, decision={result_dict.get('decision')}")
        except Exception as exc:
            result_dict = {"status": "failed", "error": str(exc), "ticket_id": l2_request_dict["ticket_id"]}
            print(f"[graph] L2 RCA node failed; error={exc}")

        return {**state, "l2_rca_result": result_dict, "status": "l2_rca_completed"}

    def fix_agent_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        l2_result = state.get("l2_rca_result", {})
        rca_data = l2_result.get("data", {})
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

    def prepare_l3_context_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        if not event.repo_url or not event.repo_full_name:
            reason = "L3 RCA requires repo_url and repo_full_name, but repo mapping is missing."
            print(f"[graph] L3 context preparation failed; {reason}")
            return {**state, "status": "missing_repo_mapping", "reason": reason}

        knowledge_id = _knowledge_id_for_repo(event)
        print(f"[graph] L3 context preparation started; event={event.id}, repo={event.repo_full_name}")
        repo_dir = _ensure_repo_checkout(event)
        _resolve_event_path(event, repo_dir)
        _ingest_repo_for_rca(repo_dir, knowledge_id)
        print(f"[graph] L3 context ready; event={event.id}, repo_dir={repo_dir}, knowledge_id={knowledge_id}")
        return {
            **state,
            "knowledge_id": knowledge_id,
            "repo_dir": repo_dir,
            "status": "ready_for_l3_rca",
        }

    def l3_rca_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state["error_event"]
        print(f"[graph] L3 RCA started; event={event.id}, error_type={event.error_type}")
        try:
            rca_result = run_l3_rca(event, state["knowledge_id"], repo_dir=state["repo_dir"])
            print(
                "[graph] L3 RCA completed; "
                f"confidence={rca_result.confidence}, file={rca_result.buggy_file}"
            )
            return {**state, "l3_rca_result": rca_result, "status": "l3_rca_completed"}
        except Exception as exc:
            print(f"[graph] L3 RCA failed; error={exc}")
            return {**state, "status": "l3_rca_failed", "reason": str(exc)}

    def save_l3_report_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        if state.get("status") != "l3_rca_completed":
            return state
        event = state["error_event"]
        print(f"[graph] Saving L3 RCA report; event={event.id}")
        report_path = save_rca_report(
            event,
            state["l3_rca_result"],
            state["knowledge_id"],
            state["repo_dir"],
        )
        print(f"[graph] L3 RCA report saved; path={report_path}")
        return {**state, "rca_report_path": report_path, "status": "l3_rca_report_saved"}

    def codefix_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        if state.get("status") != "l3_rca_report_saved":
            return state
        event = state["error_event"]
        print(f"[graph] Code fix started; event={event.id}")
        try:
            fix_result = run_code_fix(
                event,
                state["l3_rca_result"],
                state["knowledge_id"],
                repo_dir=state["repo_dir"],
            )
            print(f"[graph] Code fix completed; success={fix_result.success}")
            if fix_result.error:
                print(f"[graph] Code fix error: {fix_result.error}")
            if fix_result.pr_url:
                print(f"[graph] PR opened: {fix_result.pr_url}")
            status = "codefix_completed" if fix_result.success else "codefix_failed"
            return {**state, "codefix_result": fix_result, "status": status}
        except Exception as exc:
            print(f"[graph] Code fix failed; error={exc}")
            return {**state, "status": "codefix_failed", "reason": str(exc)}

    def build_response_node(state: IntakeCategorisationState) -> IntakeCategorisationState:
        event = state.get("error_event")
        source_event = state.get("source_event")
        l3_rca = state.get("l3_rca_result")
        codefix = state.get("codefix_result")
        response = {
            "status": state.get("status", "completed"),
            "source_event_id": source_event.id if source_event else None,
            "event_id": event.id if event else None,
            "normalised_event": normalised_event_log_payload(event) if event else None,
            "categorisation": state.get("categorisation"),
            "l1": state.get("l1_result"),
            "l2_rca": state.get("l2_rca_result"),
            "fix_agent": state.get("fix_agent_result"),
            "l3_rca": l3_rca.model_dump() if hasattr(l3_rca, "model_dump") else l3_rca,
            "l3_rca_report_path": state.get("rca_report_path"),
            "codefix": codefix.model_dump() if hasattr(codefix, "model_dump") else codefix,
            "reason": state.get("reason"),
        }
        return {**state, "response": response}

    def route_after_normalizer(state: IntakeCategorisationState) -> str:
        return "categorisation" if state.get("error_event") else "build_response"

    def route_after_categorisation(state: IntakeCategorisationState) -> str:
        categorisation = state.get("categorisation", {})
        if _is_rejected_or_review(categorisation):
            return "build_response"
        level = _support_level(categorisation)
        if level == "L1":
            return "l1_placeholder"
        if _should_route_to_l2(categorisation):
            return "l2_rca"
        if _should_route_to_l3(categorisation):
            return "prepare_l3_context"
        return "build_response"

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

    def route_after_l3_prepare(state: IntakeCategorisationState) -> str:
        return "l3_rca" if state.get("status") == "ready_for_l3_rca" else "build_response"

    def route_after_l3_rca(state: IntakeCategorisationState) -> str:
        return "save_l3_report" if state.get("status") == "l3_rca_completed" else "build_response"

    def route_after_l3_report(state: IntakeCategorisationState) -> str:
        return "codefix" if state.get("status") == "l3_rca_report_saved" else "build_response"

    graph.add_node("connector", connector_node)
    graph.add_node("normalizer", normalizer_node)
    graph.add_node("categorisation", categorisation_node)
    graph.add_node("l1_placeholder", l1_placeholder_node)
    graph.add_node("l2_rca", l2_rca_node)
    graph.add_node("fix_agent", fix_agent_node)
    graph.add_node("prepare_l3_context", prepare_l3_context_node)
    graph.add_node("l3_rca", l3_rca_node)
    graph.add_node("save_l3_report", save_l3_report_node)
    graph.add_node("codefix", codefix_node)
    graph.add_node("build_response", build_response_node)

    graph.set_entry_point("connector")
    graph.add_edge("connector", "normalizer")
    graph.add_conditional_edges(
        "normalizer",
        route_after_normalizer,
        {"categorisation": "categorisation", "build_response": "build_response"},
    )
    graph.add_conditional_edges(
        "categorisation",
        route_after_categorisation,
        {
            "l1_placeholder": "l1_placeholder",
            "l2_rca": "l2_rca",
            "prepare_l3_context": "prepare_l3_context",
            "build_response": "build_response",
        },
    )
    graph.add_edge("l1_placeholder", "build_response")
    graph.add_conditional_edges(
        "l2_rca",
        route_after_l2_rca,
        {"fix_agent": "fix_agent", "build_response": "build_response"},
    )
    graph.add_edge("fix_agent", "build_response")
    graph.add_conditional_edges(
        "prepare_l3_context",
        route_after_l3_prepare,
        {"l3_rca": "l3_rca", "build_response": "build_response"},
    )
    graph.add_conditional_edges(
        "l3_rca",
        route_after_l3_rca,
        {"save_l3_report": "save_l3_report", "build_response": "build_response"},
    )
    graph.add_conditional_edges(
        "save_l3_report",
        route_after_l3_report,
        {"codefix": "codefix", "build_response": "build_response"},
    )
    graph.add_edge("codefix", "build_response")
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
        "source": source.lower().strip(),
        "payload": payload,
        "payload_bytes": payload_bytes,
        "headers": headers,
    })
