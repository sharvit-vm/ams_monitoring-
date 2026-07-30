"""FastAPI entry point for the AMS monitoring intake + categorisation service."""

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
<<<<<<< Updated upstream

load_dotenv(override=True)

=======
from typing import Any
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

load_dotenv(override=True)

from dashboard_state import _json_safe_value, latest_execution, record_execution  # noqa: E402
>>>>>>> Stashed changes
from workflows.intake_categorisation_workflow import (  # noqa: E402
    GatewayError,
    run_intake_categorisation_workflow,
    supported_sources,
)


app = FastAPI(title="AMS Monitoring Incident Gateway")


def _dashboard_workflow_nodes() -> list[dict[str, Any]]:
    sn_instance = os.getenv("SN_INSTANCE")
    return [
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
            "id": "categorization",
            "label": "Categorisation Agent",
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
            "id": "db_fix",
            "label": "Fix Agent",
            "service": "db_fix",
            "endpoint": "in-process",
        },
        {
            "id": "servicenow",
            "label": "ServiceNow Notification",
            "service": "external",
            "endpoint": "incident notification",
            "configured_url": bool(sn_instance),
            "instance_url": sn_instance,
        },
    ]


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

<<<<<<< Updated upstream
    return JSONResponse(final_state.get("response", {"status": final_state.get("status", "completed")}), status_code=200)
=======
    response_body = final_state.get("response", {"status": final_state.get("status", "completed")})
    safe_response_body = _json_safe_value(response_body)
    record_execution(safe_response_body)
    return JSONResponse(safe_response_body, status_code=200)
>>>>>>> Stashed changes


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


<<<<<<< Updated upstream
=======
@app.get("/dashboard/workflow")
async def dashboard_workflow():
    return {
        "nodes": _dashboard_workflow_nodes(),
        "edges": [
            {"source": "connector", "target": "normalizer"},
            {"source": "normalizer", "target": "categorization"},
            {"source": "categorization", "target": "l2_rca", "condition": "support_level == L2"},
            {"source": "l2_rca", "target": "db_fix", "condition": "recommended_agent == db_fix_agent"},
            {"source": "db_fix", "target": "servicenow", "condition": "notification enabled"},
        ],
        "supported_sources": supported_sources(),
    }


@app.get("/dashboard/executions/latest")
async def dashboard_latest_execution():
    execution = latest_execution()
    if execution is None:
        return {"status": "empty", "message": "No executions recorded yet."}
    return {"status": "ok", "execution": execution}


>>>>>>> Stashed changes
@app.get("/")
async def root():
    return {
        "application": "AMS Monitoring Incident Gateway",
        "status": "running",
        "flow": "connector -> normalizer -> categorisation",
        "supported_sources": supported_sources(),
    }
