"""HTTP contract tests: all agent execution is mocked; no external requests."""

import hashlib
import hmac
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
import dashboard_state
from db_fix.api import routes
from governance.approvals import ApprovalStore, RemediationPlan
from storage.job_store import JobStore
from workflows import job_worker, intake_categorisation_workflow as workflow


class QueueHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(str(Path(self.temp.name) / "queue.sqlite3"))
        for module in (server, dashboard_state, job_worker):
            patcher = patch.object(module, "job_store", self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"JIRA_WEBHOOK_TOKEN": "test-secret", "GITHUB_WEBHOOK_SECRET": "test-secret"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(server.app)

    def test_webhook_authentication_precedes_queue_and_deduplication(self):
        payload = {"issue_key": "TEST-1", "summary": "test", "password": "do-not-store"}
        self.assertEqual(self.client.post("/webhook/jira", json=payload).status_code, 401)
        self.assertIsNone(self.store.claim())
        headers = {"X-CodeFixer-Token": "test-secret"}
        first = self.client.post("/webhook/jira", json=payload, headers=headers)
        second = self.client.post("/webhook/jira", json=payload, headers=headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(second.json()["duplicate"])
        saved = self.store.claim()["payload"]["source_event"]
        self.assertEqual(saved["headers"], {})
        self.assertNotIn("do-not-store", str(saved))
        self.assertEqual(self.client.get(first.json()["status_url"]).status_code, 200)
        self.assertEqual(self.client.get("/dashboard/executions/latest").status_code, 200)

    def test_invalid_json_and_signed_ignored_event(self):
        self.assertEqual(self.client.post("/webhook/jira", content="{").status_code, 400)
        body = b'{"action":"edited"}'
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        result = self.client.post("/webhook/github", content=body, headers={"content-type": "application/json", "X-GitHub-Event": "issues", "X-Hub-Signature-256": signature})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "ignored")
        self.assertIsNone(self.store.claim())

    def test_health_and_ack_return_while_workflow_is_blocked(self):
        started, release = threading.Event(), threading.Event()

        def slow_workflow(**kwargs):
            started.set()
            release.wait(5)
            return {"response": {"status": "completed"}}

        with patch.object(workflow, "run_intake_categorisation_workflow", slow_workflow):
            with TestClient(server.app) as client:
                try:
                    result = client.post("/webhook/jira", json={"issue_key": "TEST-2"}, headers={"X-CodeFixer-Token": "test-secret"})
                    self.assertEqual(result.status_code, 202)
                    self.assertTrue(started.wait(3))
                    self.assertEqual(client.get("/health").status_code, 200)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
            self.assertEqual(self.store.get(result.json()["job_id"])["status"], "completed")

    def test_approval_http_returns_without_running_executor(self):
        store = ApprovalStore(self.store)
        executed = []
        store.register_executor("code_fix", lambda plan: executed.append(plan))
        plan = store.create(RemediationPlan(agent_type="code_fix", target_type="repo", issue_id="TEST-3"))
        with patch.object(routes, "approval_store", store), patch.object(routes, "_record_remediation_dashboard_update"):
            response = self.client.post(f"/api/v1/remediations/{plan.approval_id}/approve", json={"approver": "tester"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["remediation_plan"]["status"], "APPROVED")
        self.assertEqual(executed, [])


if __name__ == "__main__":
    unittest.main()
