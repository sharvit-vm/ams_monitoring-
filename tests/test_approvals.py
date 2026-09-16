import unittest

from governance.approvals import ApprovalStore, RemediationPlan


class ApprovalStoreTests(unittest.TestCase):
    def test_approval_plan_preserves_workflow_session_id(self):
        store = ApprovalStore()
        observed = []
        store.register_executor("code_fix", lambda current: observed.append(current.session_id) or {"status": "EXECUTED"})
        plan = store.create(RemediationPlan(
            agent_type="code_fix",
            target_type="code_repository",
            issue_id="INC-099",
            session_id="workflow-session-099",
        ))

        store.approve_and_execute(plan.approval_id, approver="reviewer@example.com")

        self.assertEqual(observed, ["workflow-session-099"])

    def test_approval_publishes_before_and_after_execution(self):
        store = ApprovalStore()
        updates = []
        store.register_executor("code_fix", lambda _: {"status": "EXECUTED", "success": True})
        plan = store.create(RemediationPlan(
            agent_type="code_fix",
            target_type="code_repository",
            issue_id="INC-100",
        ))

        result = store.approve_and_execute(
            plan.approval_id,
            approver="reviewer@example.com",
            on_update=lambda current: updates.append(current.status),
        )

        self.assertEqual(updates, ["APPROVED", "EXECUTED"])
        self.assertEqual(result.status, "EXECUTED")
        self.assertTrue(result.execution_result["success"])

    def test_failed_result_without_status_is_not_marked_executed(self):
        store = ApprovalStore()
        store.register_executor("code_fix", lambda _: {"success": False, "error": "Patch failed"})
        plan = store.create(RemediationPlan(
            agent_type="code_fix",
            target_type="code_repository",
            issue_id="INC-101",
        ))

        result = store.approve_and_execute(plan.approval_id, approver="reviewer@example.com")

        self.assertEqual(result.status, "EXECUTION_FAILED")


if __name__ == "__main__":
    unittest.main()
