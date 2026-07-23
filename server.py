"""FastAPI entry point for the AMS monitoring intake + categorisation service."""

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv(override=True)

from workflows.intake_categorisation_workflow import (  # noqa: E402
    GatewayError,
    run_intake_categorisation_workflow,
    supported_sources,
)


app = FastAPI(title="AMS Monitoring Incident Gateway")


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

    return JSONResponse(final_state.get("response", {"status": final_state.get("status", "completed")}), status_code=200)


@app.post("/webhook/incidents/{source}")
async def incident_gateway(source: str, request: Request):
    return await _run_gateway(source, request, include_raw_body=source.lower().strip() == "github")


@app.post("/webhook/github")
async def github_webhook(request: Request):
    return await _run_gateway("github", request, include_raw_body=True)


@app.post("/webhook/jira")
async def jira_webhook(request: Request):
    return await _run_gateway("jira", request)


@app.post("/webhook/servicenow")
async def servicenow_webhook(request: Request):
    return await _run_gateway("servicenow", request)


@app.get("/health")
async def health():
    return {"status": "ok", "supported_sources": supported_sources()}


@app.get("/")
async def root():
    return {
        "application": "AMS Monitoring Incident Gateway",
        "status": "running",
        "flow": "connector -> normalizer -> categorisation",
        "supported_sources": supported_sources(),
    }
