"""Durable jobs and execution snapshots for a single-host deployment."""

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("WORKFLOW_DB_PATH", "data/workflow.sqlite3")

    @contextmanager
    def connection(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, dedupe TEXT, payload TEXT, status TEXT, created TEXT, updated TEXT, error TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, created)")
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, data TEXT, updated TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, data TEXT)")
            yield db

    def enqueue(self, kind: str, payload: dict, dedupe: str, *, job_id: str | None = None) -> tuple[str, bool]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE kind=? AND dedupe=? AND status NOT IN ('failed','interrupted')", (kind, dedupe)).fetchone()
            if row:
                return row["id"], False
            identity = job_id or str(uuid4())
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (identity, kind, dedupe, json.dumps(payload), "pending", now(), now(), None))
            if kind == "incident":
                execution = {"id": identity, "recorded_at": now(), "response": {"status": "pending", "source": payload.get("source"), "workflow_session_id": identity}}
                db.execute("INSERT INTO snapshots VALUES (?,?,?)", (identity, json.dumps(execution), now()))
            return identity, True

    def claim(self) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (now(), row["id"]))
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            return result

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?, updated=?, error=? WHERE id=?", (status, now(), error, job_id))

    def recover(self) -> int:
        # A stopped thread may have already pushed a PR or changed a database.
        # Do not automatically replay side effects whose outcome is unknown.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                identity = row["id"]
                plan = None
                if row["kind"] == "remediation":
                    approval_id = json.loads(row["payload"])["approval_id"]
                    saved = db.execute("SELECT data FROM approvals WHERE id=?", (approval_id,)).fetchone()
                    if saved:
                        plan = json.loads(saved["data"])
                        plan.update(status="EXECUTION_FAILED", execution_result={"status": "EXECUTION_FAILED", "error": "interrupted", "message": "Worker stopped; review side effects before retrying"}, updated_at=now())
                        db.execute("UPDATE approvals SET data=? WHERE id=?", (json.dumps(plan), approval_id))
                        identity = plan.get("session_id") or identity
                saved = db.execute("SELECT data FROM snapshots WHERE id=?", (identity,)).fetchone()
                if saved:
                    execution = json.loads(saved["data"])
                    execution["response"].update(status="failed", error="interrupted")
                    if plan is not None:
                        execution["response"]["fix_agent"] = plan
                    execution["recorded_at"] = now()
                    db.execute("UPDATE snapshots SET data=?, updated=? WHERE id=?", (json.dumps(execution), now(), identity))
            return db.execute("UPDATE jobs SET status='interrupted', updated=?, error='Worker stopped; review before resubmitting' WHERE status='running'", (now(),)).rowcount

    def get(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT id,kind,status,created,updated,error FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def save_snapshot(self, identity: str, execution: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (identity, json.dumps(execution), now()))

    def snapshot(self, identity: str | None = None) -> dict | None:
        with self.connection() as db:
            row = (db.execute("SELECT data FROM snapshots WHERE id=?", (identity,)).fetchone() if identity else db.execute("SELECT data FROM snapshots ORDER BY updated DESC LIMIT 1").fetchone())
            return json.loads(row["data"]) if row else None

    def save_approval(self, plan: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO approvals VALUES (?,?)", (plan["approval_id"], json.dumps(plan)))

    def approvals(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row["data"]) for row in db.execute("SELECT data FROM approvals")]


job_store = JobStore()
