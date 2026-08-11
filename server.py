"""FastAPI entry point for the AMS monitoring intake + categorisation service."""

import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

load_dotenv(override=True)

from dashboard_state import _json_safe_value, latest_execution, record_execution  # noqa: E402
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
            "service": "governance",
            "endpoint": "internal:validate_guardrails",
        },
        {
            "id": "categorization",
            "label": "Categorization Agent",
            "service": "categorization_layer",
            "endpoint": "internal:categorise_error_event",
        },
        {
            "id": "l2_rca",
            "label": "L2 RCA Agent",
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
            "label": "L3 RCA Agent",
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
            "configured_url": bool(jira_instance),
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
        final_state = run_intake_categorisation_workflow(
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
        return {"status": "empty", "message": "No executions recorded yet."}
    return {"status": "ok", "execution": execution}


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
