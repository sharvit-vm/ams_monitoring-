"""FastAPI entry point for the AMS monitoring intake + categorisation service."""

import json
import os
from queue import Empty

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from typing import Any


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

load_dotenv(override=True)

from dashboard_state import (  # noqa: E402
    _json_safe_value,
    latest_execution,
    record_execution,
    subscribe_executions,
    unsubscribe_executions,
)
from workflows.intake_categorisation_workflow import (  # noqa: E402
    GatewayError,
    run_intake_categorisation_workflow,
    supported_sources,
)
from db_fix.api.routes import router as db_fix_router  # noqa: E402
from governance.approvals import approval_store  # noqa: E402


app = FastAPI(title="AMS Monitoring Incident Gateway")

_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(db_fix_router)


NOTIFICATION_CHANNELS = {
    "servicenow": {
        "id": "servicenow",
        "label": "ServiceNow Notification",
        "service": "external",
        "endpoint": "incident notification",
    },
    "jira": {
        "id": "jira",
        "label": "Jira Notification",
        "service": "external",
        "endpoint": "incident notification",
    },
    "github": {
        "id": "github",
        "label": "GitHub Notification",
        "service": "external",
        "endpoint": "incident notification",
    },
}


def notification_channel_for_source(source: str | None) -> str:
    source_key = (source or "").lower().strip().replace("_issue", "")
    if source_key in {"jira"}:
        return "jira"
    if source_key in {"github"}:
        return "github"
    return "servicenow"


def _dashboard_workflow_nodes() -> list[dict[str, Any]]:
    sn_instance = os.getenv("SN_INSTANCE")
    jira_instance = (
        os.getenv("JIRA_INSTANCE")
        or os.getenv("JIRA_BASE_URL")
        or os.getenv("JIRA_URL")
    )
    github_instance = os.getenv("GITHUB_REPOSITORY_URL") or os.getenv("GITHUB_URL")
    nodes = [
        {
            "id": "connector",
            "label": "Connector",
            "service": "intake_gateway",
            "endpoint": "/webhook/incidents/{source}",
        },
        {
            "id": "normalizer",
            "label": "Normalizer",
            "service": "intake_gateway",
            "endpoint": "internal:normalise_source_event",
        },
        {
            "id": "guardrails",
            "label": "AI Guardrails",
            "service": "Security & Policy Validation",
            "endpoint": "internal:validate_guardrails",
        },
        {
            "id": "categorization",
            "label": "Categorization",
            "service": "categorization_layer",
            "endpoint": "internal:categorise_error_event",
        },
        {
            "id": "l2_rca",
            "label": "L2 RCA",
            "service": "l2_rca",
            "endpoint": "in-process",
        },
        {
            "id": "human_approval",
            "label": "Human Approval",
            "service": "governance",
            "endpoint": "manual approval gate",
        },
        {
            "id": "l1_placeholder",
            "label": "L1 Placeholder",
            "service": "workflow",
            "endpoint": "in-process",
        },
        {
            "id": "l3_rca",
            "label": "L3 RCA",
            "service": "agents.l3_rca",
            "endpoint": "in-process",
        },
        {
            "id": "codefix",
            "label": "Codefix Agent",
            "service": "agents.code_fix",
            "endpoint": "in-process",
        },
        {
            "id": "db_fix",
            "label": "Fix Agent",
            "service": "db_fix",
            "endpoint": "in-process",
        },
        {
            **NOTIFICATION_CHANNELS["servicenow"],
            "configured_url": bool(sn_instance),
            "instance_url": sn_instance,
        },
        {
            **NOTIFICATION_CHANNELS["jira"],
            "configured_url": bool(jira_instance),
            "instance_url": jira_instance,
        },
        {
            **NOTIFICATION_CHANNELS["github"],
            "configured_url": bool(github_instance),
            "instance_url": github_instance,
        },
    ]
    return nodes


def _dashboard_source_platforms() -> list[dict[str, Any]]:
    sn_instance = os.getenv("SN_INSTANCE")
    jira_instance = (
        os.getenv("JIRA_INSTANCE")
        or os.getenv("JIRA_BASE_URL")
        or os.getenv("JIRA_URL")
    )
    jira_create_issue_url = os.getenv("JIRA_CREATE_ISSUE_URL")
    github_instance = os.getenv("GITHUB_REPOSITORY_URL") or os.getenv("GITHUB_URL")
    platforms = [
        {
            "id": "servicenow",
            "label": "ServiceNow",
            "source": "servicenow",
            "instance_url": sn_instance,
            "configured_url": bool(sn_instance),
        },
        {
            "id": "jira",
            "label": "Jira",
            "source": "jira",
            "instance_url": jira_instance,
            "create_url": jira_create_issue_url,
            "configured_url": bool(jira_instance or jira_create_issue_url),
        },
        {
            "id": "github",
            "label": "GitHub",
            "source": "github",
            "instance_url": github_instance,
            "configured_url": bool(github_instance),
        },
    ]
    supported = set(supported_sources())
    return [platform for platform in platforms if platform["source"] in supported]


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _status_complete(value: Any) -> bool:
    return str(value or "").strip().upper() in {
        "COMPLETED",
        "SUCCESS",
        "SUCCEEDED",
        "SUCCESSFUL",
        "EXECUTED",
        "DONE",
        "RESOLVED",
        "HEALTHY",
        "PASSED",
        "L1_PLACEHOLDER_COMPLETED",
    }


def _confidence_percent(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(value * 100) if value <= 1 else round(value)
    return None


def _duration_ms(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _dashboard_execution_summary(execution: dict[str, Any]) -> dict[str, Any]:
    response = _nested_dict(execution.get("response"))
    metrics = _nested_dict(response.get("metrics"))
    categorisation = _nested_dict(response.get("categorization") or response.get("categorisation"))
    l2_response = _nested_dict(response.get("l2_response") or response.get("l2Rca") or response.get("l2"))
    db_execution = _nested_dict(response.get("db_execution") or response.get("db_fix") or response.get("remediation"))
    db_metrics = _nested_dict(db_execution.get("metrics"))

    total_steps = len([
        node for node in _dashboard_workflow_nodes()
        if node["id"] not in {"l1_placeholder", "codefix"}
    ])
    completed = 0
    completed += 1 if _status_complete(response.get("status")) else 0
    completed += 1 if _nested_dict(response.get("normalised") or response.get("normalized") or response.get("event")) else 0
    completed += 1 if _nested_dict(response.get("guardrails")) else 0
    completed += 1 if categorisation else 0
    completed += 1 if _status_complete(db_execution.get("status") or db_execution.get("overall_status")) else 0

    confidence_candidates = [
        _confidence_percent(response.get("overall_confidence")),
        _confidence_percent(categorisation.get("confidence")),
        _confidence_percent(l2_response.get("confidence")),
        _confidence_percent(db_execution.get("confidence")),
    ]
    overall_confidence = next((value for value in confidence_candidates if value is not None), None)

    duration_candidates = [
        _duration_ms(metrics.get("total_duration_ms")),
        _duration_ms(metrics.get("duration_ms")),
        _duration_ms(db_metrics.get("total_duration_ms")),
        _duration_ms(_nested_dict(db_metrics.get("stage_durations_ms")).get("total")),
    ]
    workflow_time_ms = next((value for value in duration_candidates if value is not None), None)

    return {
        "total_steps": total_steps,
        "completed": completed,
        "overall_confidence": overall_confidence,
        "workflow_time_ms": workflow_time_ms,
    }


async def _run_gateway(source: str, request: Request, *, include_raw_body: bool = False):
    source_key = source.lower().strip()
    if source_key not in supported_sources():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unsupported_source",
                "source": source,
                "supported_sources": supported_sources(),
            },
        )

    payload_bytes = await request.body() if include_raw_body else b""
    payload = await request.json()
    if include_raw_body and not payload_bytes:
        payload_bytes = str(payload).encode("utf-8")

    try:
        print(f"[gateway] Received {source_key} webhook; running workflow in threadpool")
        final_state = await run_in_threadpool(
            run_intake_categorisation_workflow,
            source=source_key,
            payload=payload,
            headers=dict(request.headers),
            payload_bytes=payload_bytes,
        )
    except GatewayError as exc:
        if exc.status_code == 200:
            return JSONResponse({"status": "ignored", "reason": exc.detail}, status_code=200)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    response_body = final_state.get("response", {"status": final_state.get("status", "completed")})
    safe_response_body = _json_safe_value(response_body)
    record_execution(safe_response_body)
    print(f"[gateway] Completed {source_key} webhook; status={safe_response_body.get('status')}")
    return JSONResponse(safe_response_body, status_code=200)


@app.post("/webhook/incidents/{source}")
async def incident_gateway(source: str, request: Request, body: WebhookPayload = None):
    return await _run_gateway(source, request, include_raw_body=source.lower().strip() == "github")


@app.post("/webhook/github")
async def github_webhook(request: Request, body: WebhookPayload = None):
    return await _run_gateway("github", request, include_raw_body=True)


@app.post("/webhook/jira")
async def jira_webhook(request: Request, body: WebhookPayload = None):
    return await _run_gateway("jira", request)


@app.post("/webhook/servicenow")
async def servicenow_webhook(request: Request, body: WebhookPayload = None):
    return await _run_gateway("servicenow", request)


@app.get("/health")
async def health():
    return {"status": "ok", "supported_sources": supported_sources()}


@app.get("/dashboard/workflow")
async def dashboard_workflow():
    return {
        "nodes": _dashboard_workflow_nodes(),
        "edges": [
            {"source": "connector", "target": "normalizer"},
            {"source": "normalizer", "target": "guardrails"},
            {"source": "guardrails", "target": "categorization", "condition": "guardrails.allowed == true"},
            {"source": "categorization", "target": "l1_placeholder", "condition": "support_level == L1"},
            {"source": "categorization", "target": "l2_rca", "condition": "support_level == L2"},
            {"source": "categorization", "target": "l3_rca", "condition": "support_level == L3"},
            {"source": "l2_rca", "target": "human_approval", "condition": "recommended_agent == db_fix_agent"},
            {"source": "human_approval", "target": "db_fix", "condition": "approval == approved"},
            {"source": "l3_rca", "target": "codefix", "condition": "confidence != low"},
            {"source": "build_response", "target": "servicenow", "condition": "source == servicenow"},
            {"source": "build_response", "target": "jira", "condition": "source == jira"},
            {"source": "build_response", "target": "github", "condition": "source == github"},
        ],
        "supported_sources": supported_sources(),
        "source_platforms": _dashboard_source_platforms(),
    }


@app.get("/dashboard/executions/latest")
async def dashboard_latest_execution():
    execution = latest_execution()
    if execution is None:
        return {"status": "empty", "message": "No executions recorded yet.", "summary": None}
    return {
        "status": "ok",
        "execution": execution,
        "summary": _dashboard_execution_summary(execution),
    }


@app.get("/dashboard/executions/stream")
async def dashboard_execution_stream(request: Request):
    queue = subscribe_executions()
    print("[dashboard] SSE client connected")

    async def events():
        try:
            latest = latest_execution()
            if latest is not None:
                yield f"event: execution\ndata: {json.dumps(latest, default=str)}\n\n"
            while not await request.is_disconnected():
                try:
                    execution = await run_in_threadpool(queue.get, True, 15)
                    yield f"event: execution\ndata: {json.dumps(execution, default=str)}\n\n"
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            unsubscribe_executions(queue)
            print("[dashboard] SSE client disconnected")

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/dashboard/approvals/pending")
async def dashboard_pending_approvals():
    plans = approval_store.list(status="WAITING_FOR_APPROVAL")
    return {
        "status": "ok",
        "approvals": [plan.model_dump() for plan in plans],
    }


@app.get("/")
async def root():
    return {
        "application": "AMS Monitoring Incident Gateway",
        "status": "running",
        "flow": "connector -> normalizer -> categorisation -> L1/L2/L3 route",
        "supported_sources": supported_sources(),
    }

