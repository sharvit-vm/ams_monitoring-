import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from storage.job_store import JobStore
from governance.approvals import ApprovalStore, RemediationPlan


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(str(Path(self.temp.name) / "jobs.sqlite3"))

    def test_deduplication_and_pending_jobs_survive_restart(self):
        identity, created = self.store.enqueue("incident", {"source": "jira"}, "delivery-1")
        self.assertTrue(created)
        reopened = JobStore(self.store.path)
        self.assertEqual(reopened.enqueue("incident", {}, "delivery-1"), (identity, False))
        self.assertEqual(reopened.claim()["id"], identity)
        self.assertIsNone(self.store.claim())

    def test_two_connections_cannot_claim_same_job(self):
        self.store.enqueue("incident", {}, "one")
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: JobStore(self.store.path).claim(), range(2)))
        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_interrupted_work_is_not_blindly_replayed(self):
        identity, _ = self.store.enqueue("incident", {}, "one")
        self.store.claim()
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.get(identity)["status"], "interrupted")
        self.assertIsNone(self.store.claim())

    def test_approval_enqueues_without_executing_and_survives_restart(self):
        approvals = ApprovalStore(self.store)
        executed = []
        approvals.register_executor("code_fix", lambda plan: executed.append(plan.session_id) or {"status": "EXECUTED"})
        plan = approvals.create(RemediationPlan(agent_type="code_fix", target_type="repo", issue_id="one", session_id="original-session", trace_context={"trace_id": "trace"}))
        approved, identity = approvals.enqueue_approval(plan.approval_id, approver="tester")
        self.assertEqual(executed, [])
        self.assertEqual(self.store.get(identity)["status"], "pending")
        reopened = ApprovalStore(self.store)
        self.assertEqual(reopened.get(plan.approval_id).trace_context, {"trace_id": "trace"})
        reopened.register_executor("code_fix", lambda current: executed.append(current.session_id) or {"status": "EXECUTED"})
        self.store.claim()
        reopened.execute_approved(plan.approval_id)
        self.assertEqual(executed, ["original-session"])
        with self.assertRaises(ValueError):
            reopened.enqueue_approval(plan.approval_id, approver="tester")

    def test_snapshot_isolation(self):
        self.store.save_snapshot("a", {"response": {"status": "waiting"}})
        self.store.save_snapshot("b", {"response": {"status": "completed"}})
        self.assertEqual(self.store.snapshot("a")["response"]["status"], "waiting")

    def test_worker_serializes_jobs_and_preserves_session(self):
        from workflows import job_worker
        import sys
        from types import SimpleNamespace
        from issuelayer.intake.source_event import SourceEvent
        started, release = threading.Event(), threading.Event()
        seen = []

        def workflow(**kwargs):
            seen.append(kwargs["workflow_session_id"])
            started.set()
            release.wait(5)
            return {"response": {"status": "completed"}}

        source_event = SourceEvent(source="jira", event_type="created")
        identity, _ = self.store.enqueue("incident", {"source": "jira", "source_event": source_event.model_dump(mode="json")}, "one")
        with patch.object(job_worker, "job_store", self.store), patch.object(job_worker, "record_execution"), patch.dict(sys.modules, {"workflows.intake_categorisation_workflow": SimpleNamespace(run_intake_categorisation_workflow=workflow)}):
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(job_worker.run_next_job)
                try:
                    self.assertTrue(started.wait(3))
                    second, _ = self.store.enqueue("incident", {}, "two")
                    self.assertEqual(self.store.get(second)["status"], "pending")
                    self.assertEqual(self.store.get(identity)["status"], "running")
                finally:
                    release.set()
                self.assertTrue(task.result(timeout=5))
        self.assertEqual(seen, [identity])
        self.assertEqual(self.store.get(identity)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
