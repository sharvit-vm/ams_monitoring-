"""
server.py - FastAPI webhook server
"""

import asyncio
import contextlib
import hashlib
import os
import re
import subprocess
import traceback
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv(override=True)

from issuelayer.connectors.github_connector import handle_github_issue_event
from issuelayer.connectors.jira_connector import handle_jira_event
from issuelayer.connectors.servicenow_connector import handle_servicenow_event
from issuelayer.intake.queue import EventQueue
from issuelayer.intake.schemas import ErrorEvent

app = FastAPI(title="CodeFix Webhook Server")
queue = EventQueue()
worker_task = None

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
CLONE_ROOT = os.getenv("CLONE_ROOT", "clone")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
JIRA_WEBHOOK_TOKEN = os.getenv("JIRA_WEBHOOK_TOKEN", "")
SERVICENOW_WEBHOOK_TOKEN = os.getenv("SERVICENOW_WEBHOOK_TOKEN", "")
REPORTS_READ_TOKEN = os.getenv("REPORTS_READ_TOKEN", "")
AUTO_INGEST_ON_WEBHOOK = os.getenv("AUTO_INGEST_ON_WEBHOOK", "true").lower() == "true"
AUTO_VECTOR_INGEST_ON_WEBHOOK = os.getenv("AUTO_VECTOR_INGEST_ON_WEBHOOK", "false").lower() == "true"


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


def _git(args: list, cwd: str, check: bool = False):
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
    """
    Convert runtime traceback paths to repo-relative paths so RCA/fix tools can
    find the file in the cloned repository.
    """
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
    print(f"[worker] Resolved event path: {event.file_path} -> {resolved}")
    event.file_path = resolved


def _ingest_repo_for_rca(repo_dir: str, knowledge_id: str):
    """
    Build the Neo4j knowledge graph for the checked-out repo before RCA.
    Vector ingestion is optional because the current RCA/code-fix tools use
    Neo4j plus direct file reads, and Pinecone ingestion can be expensive.
    """
    if not AUTO_INGEST_ON_WEBHOOK:
        print("[worker] AUTO_INGEST_ON_WEBHOOK=false; skipping repo ingestion")
        return

    from models import PipelineState
    from phases.file_analysis import analyze_files
    from phases.hierarchy import build_hierarchy
    from phases.llm_analysis import analyze_with_llm
    from phases.neo4j_ingest import neo4j_ingest
    from phases.scanner import scan_repo

    print(f"[worker] Ingesting repo into Neo4j; knowledge_id={knowledge_id}")
    state = PipelineState(repo_path=repo_dir, knowledge_id=knowledge_id)
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)
    state = build_hierarchy(state)
    state = neo4j_ingest(state)

    if AUTO_VECTOR_INGEST_ON_WEBHOOK:
        from phases.vector_ingest import vector_ingest

        print(f"[worker] Ingesting repo into Pinecone; knowledge_id={knowledge_id}")
        state = vector_ingest(state)

    print(f"[worker] Repo ingestion ready; files={len(state.files)}, knowledge_id={knowledge_id}")


def _run_rca_and_fix(event: ErrorEvent):
    try:
        from workflows.ams_code_workflow import AMSWorkflowDeps, run_ams_code_workflow

        print(f"[worker] Starting AMS LangGraph workflow for event {event.id}")
        deps = AMSWorkflowDeps(
            queue=queue,
            knowledge_id_for_repo=_knowledge_id_for_repo,
            ensure_repo_checkout=_ensure_repo_checkout,
            resolve_event_path=_resolve_event_path,
            ingest_repo_for_rca=_ingest_repo_for_rca,
        )
        final_state = run_ams_code_workflow(event, deps)
        print(f"[worker] AMS workflow finished for event {event.id}; status={final_state.get('status')}")

    except Exception as e:
        print(f"[worker] Pipeline failed for {event.id}: {e}")
        print(traceback.format_exc())
        queue.update_status(event.id, "failed", extra={"error": str(e)})


async def _queue_worker():
    recovered = queue.reset_interrupted()
    if recovered:
        print(f"[worker] Re-queued {recovered} interrupted event(s) after startup")

    print("[worker] Queue worker started")
    while True:
        event = queue.claim_next()
        if event is None:
            await asyncio.sleep(2)
            continue

        print(f"[worker] Claimed event {event.id} ({event.error_type})")
        await asyncio.to_thread(_run_rca_and_fix, event)


@app.on_event("startup")
async def start_queue_worker():
    global worker_task
    if worker_task is None or worker_task.done():
        worker_task = asyncio.create_task(_queue_worker())


@app.on_event("shutdown")
async def stop_queue_worker():
    global worker_task
    if worker_task is None:
        return
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
    worker_task = None


@app.post("/webhook/github")
async def github_webhook(request: Request):
    payload_bytes = await request.body()
    payload = await request.json()
    result = handle_github_issue_event(
        payload_bytes=payload_bytes,
        payload=payload,
        headers=dict(request.headers),
        queue=queue,
        webhook_secret=WEBHOOK_SECRET,
        knowledge_id_for_repo=_knowledge_id_for_repo,
    )
    if result.http_status == 401:
        raise HTTPException(status_code=401, detail=result.body.get("detail", "Unauthorized"))
    return JSONResponse(result.as_response_body(), status_code=result.http_status)


@app.post("/webhook/jira")
async def jira_webhook(request: Request):
    payload = await request.json()
    result = handle_jira_event(
        payload=payload,
        headers=dict(request.headers),
        queue=queue,
        webhook_token=JIRA_WEBHOOK_TOKEN,
        knowledge_id_for_repo=_knowledge_id_for_repo,
    )
    if result.http_status == 401:
        raise HTTPException(status_code=401, detail=result.body.get("detail", "Unauthorized"))
    return JSONResponse(result.as_response_body(), status_code=result.http_status)


@app.post("/webhook/servicenow")
async def servicenow_webhook(request: Request):
    payload = await request.json()
    result = handle_servicenow_event(
        payload=payload,
        headers=dict(request.headers),
        queue=queue,
        webhook_token=SERVICENOW_WEBHOOK_TOKEN,
        knowledge_id_for_repo=_knowledge_id_for_repo,
    )
    if result.http_status == 401:
        raise HTTPException(status_code=401, detail=result.body.get("detail", "Unauthorized"))
    return JSONResponse(result.as_response_body(), status_code=result.http_status)


@app.get("/queue")
async def get_queue():
    return {"stats": queue.stats(), "events": queue.all_records()}


@app.get("/queue/{event_id}")
async def get_event(event_id: str):
    event = queue.get_record(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/reports/rca/{event_id}")
async def get_rca_report(event_id: str, request: Request):
    from storage.rca_report_store import load_rca_report

    if REPORTS_READ_TOKEN:
        supplied_token = request.headers.get("X-Reports-Token") or request.headers.get("X-CodeFixer-Token")
        if supplied_token != REPORTS_READ_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")

    report = load_rca_report(event_id)
    if not report:
        raise HTTPException(status_code=404, detail="RCA report not found")
    return report


@app.get("/health")
async def health():
    return {"status": "ok", "queue_stats": queue.stats()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
