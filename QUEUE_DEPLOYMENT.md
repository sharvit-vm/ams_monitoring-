# Background workflow execution

Webhooks now authenticate and persist an incident job, then return HTTP 202 with
`job_id`, `workflow_session_id`, `duplicate`, and `status_url`. This is an API
behavior change: the webhook response no longer contains the finished RCA.
Read `GET /dashboard/jobs/{job_id}` for job state and the saved execution, or use
the existing dashboard polling/SSE endpoints. Job endpoints contain incident data;
apply the same access controls as the existing dashboard.

The worker runs the existing graph in a thread. Approval requests persist the
decision and enqueue remediation in one SQLite transaction, returning HTTP 202
with the existing `status` and `remediation_plan` fields plus `job_id`.
Approval plans, trace/session identifiers, and dashboard snapshots survive restart.
Queued request authentication headers and signed raw bodies are not stored.

## Run on Windows EC2

From the repository root, with the environment configured:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
```

Use `venv` instead of `.venv` if that is the local environment directory.
Do not use `--reload` for deployment. One worker processes incidents and approved
fixes sequentially, independently of HTTP requests. A process lock rejects a second
worker on the same database. Shared repository checkouts and in-memory SSE still
require one application process. Increasing Uvicorn workers is not supported.

Optional configuration (standard-library SQLite; no broker installation):

```dotenv
WORKFLOW_DB_PATH=data/workflow.sqlite3
```

Keep this database on persistent local disk, restrict filesystem access, and back
it up with SQLite-aware tooling. It contains incident and remediation context.
Render requires persistent storage; ephemeral disk cannot preserve jobs across deploys.

Pending jobs resume on startup. A job that was running during a crash is marked
`interrupted` for review, since it may already have changed a database or pushed a
PR. It is not automatically replayed. Graceful shutdown waits for the active job.
Do not delete the job database to restart the service.

An identical source/payload (or supplied X-Idempotency-Key / GitHub delivery ID)
returns the existing job. Use a new idempotency key for an intentional new run.
Failed/interrupted incident jobs can be resubmitted after investigation. Review
interrupted remediation manually before authorizing another fix.

This change reduces HTTP waiting time; it does not shorten LLM or ingestion work.
Concurrent incidents need isolated Git checkouts, bounded model concurrency, and
shared dashboard notifications before adding worker parallelism. Existing internal
ingestion parallelism remains in place.

## Verify without external calls

```powershell
.\venv\Scripts\python.exe -B -m unittest tests.test_job_queue tests.test_approvals
```

For an integration check, submit a valid webhook, confirm immediate HTTP 202, poll
its status_url, and verify the dashboard still responds while RCA runs. Approve
the saved remediation plan and confirm another immediate HTTP 202 followed by
worker progress in the same incident session.
