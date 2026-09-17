"""One durable worker, independent of webhook and approval request lifetimes."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dashboard_state import execution_id, record_execution
from storage.job_store import job_store

logger = logging.getLogger(__name__)


@contextmanager
def worker_lock():
    """Shared checkouts and in-process SSE require one application process."""
    path = Path(job_store.path + ".worker.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A workflow worker is already running. Use one Uvicorn process.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run_next_job() -> bool:
    job = job_store.claim()
    if job is None:
        return False
    identity = job["id"]
    token = execution_id.set(identity)
    try:
        logger.info("[worker] Claimed job %s (%s)", identity, job["kind"])
        if job["kind"] == "incident":
            from issuelayer.intake.source_event import SourceEvent
            from workflows.intake_categorisation_workflow import run_intake_categorisation_workflow
            source_event = SourceEvent(**job["payload"]["source_event"])
            result = run_intake_categorisation_workflow(
                source=job["payload"]["source"],
                payload=source_event.raw_payload,
                headers={},
                source_event=source_event,
                workflow_session_id=identity,
            )
            response = result.get("response", {"status": result.get("status", "completed")})
            record_execution({**response, "workflow_session_id": identity})
            status = str(response.get("status", "completed"))
            job_store.finish(identity, "failed" if "failed" in status.lower() else "completed")
        elif job["kind"] == "remediation":
            from governance.approvals import approval_store
            from db_fix.api.routes import _record_remediation_dashboard_update
            # Import registers the executor after a restart, before any new RCA.
            import agents.code_fix  # noqa: F401
            plan = approval_store.get(job["payload"]["approval_id"])
            if plan is None:
                raise ValueError("Approval plan is missing")
            execution_id.set(plan.session_id or identity)
            plan = approval_store.execute_approved(plan.approval_id, on_update=_record_remediation_dashboard_update)
            failed = "FAIL" in plan.status.upper() or plan.status == "APPROVED_NO_EXECUTOR"
            job_store.finish(identity, "failed" if failed else "completed")
        else:
            raise ValueError("Unknown job kind")
    except Exception as exc:
        # Persist error types only; exception messages may contain credentials.
        logger.error("[worker] Job %s failed (%s)", identity, type(exc).__name__)
        job_store.finish(identity, "failed", type(exc).__name__)
        if job["kind"] == "incident":
            previous = job_store.snapshot(identity)
            response = dict(previous["response"]) if previous else {}
            record_execution({**response, "status": "failed", "error": type(exc).__name__, "workflow_session_id": identity})
    finally:
        execution_id.reset(token)
    return True


async def worker_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            worked = await asyncio.to_thread(run_next_job)
        except Exception as exc:
            logger.error("[worker] Queue unavailable (%s)", type(exc).__name__)
            worked = False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass


@asynccontextmanager
async def workflow_lifespan(app):
    with worker_lock():
        recovered = await asyncio.to_thread(job_store.recover)
        from governance.approvals import approval_store
        await asyncio.to_thread(approval_store.reload)
        if recovered:
            logger.warning("[worker] %s interrupted jobs need review", recovered)
        stop = asyncio.Event()
        task = asyncio.create_task(worker_loop(stop))
        app.state.workflow_worker = task
        try:
            yield
        finally:
            stop.set()
            # Do not cancel a thread while it is changing a repo or database.
            await task
