from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field
from storage.job_store import JobStore, job_store


class RemediationPlan(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_type: str
    target_type: str
    status: str = "WAITING_FOR_APPROVAL"
    source_platform: str = "unknown"
    issue_id: str
    issue_summary: str = ""
    severity: str = "medium"
    confidence: float = 0.0
    recommended_action: str = ""
    expected_impact: str = ""
    estimated_execution_time: str = ""
    risk_level: str = "medium"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    execution_context: dict[str, Any] = Field(default_factory=dict)
    # Stable correlation ID for the complete incident workflow, including
    # approval-resumed execution.
    session_id: str = ""
    # Langfuse trace context used to attach approval-resumed work to the
    # original incident trace.
    trace_context: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: _now_iso())
    updated_at: str = Field(default_factory=lambda: _now_iso())
    approved_by: str | None = None
    approval_reason: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    execution_result: dict[str, Any] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStore:
    """Thread-safe approval store with optional durable single-host storage."""

    def __init__(self, storage: JobStore | None = None) -> None:
        self._storage = storage
        self._plans: dict[str, RemediationPlan] = {
            item["approval_id"]: RemediationPlan(**item)
            for item in (storage.approvals() if storage else [])
        }
        self._executors: dict[str, Callable[[RemediationPlan], dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def reload(self) -> None:
        if self._storage:
            with self._lock:
                self._plans = {item["approval_id"]: RemediationPlan(**item) for item in self._storage.approvals()}

    def create(self, plan: RemediationPlan) -> RemediationPlan:
        with self._lock:
            from dashboard_state import execution_id
            plan.session_id = plan.session_id or execution_id.get() or ""
            plan.status = plan.status or "WAITING_FOR_APPROVAL"
            plan.updated_at = _now_iso()
            self._plans[plan.approval_id] = plan
            self._persist(plan)
            return plan

    def _persist(self, plan: RemediationPlan) -> None:
        if self._storage:
            self._storage.save_approval(plan.model_dump(mode="json"))

    def enqueue_approval(self, approval_id: str, *, approver: str, reason: str = "") -> tuple[RemediationPlan, str]:
        """Persist the approval decision and continuation in one transaction."""
        if self._storage is None:
            raise RuntimeError("Durable approval storage is required")
        with self._lock:
            plan = self._require_plan(approval_id)
            if plan.status != "WAITING_FOR_APPROVAL":
                raise ValueError(f"Plan {approval_id} is not awaiting approval")
            updated = plan.model_copy(deep=True)
            updated.status = "APPROVED"
            updated.approved_by = approver
            updated.approval_reason = reason
            updated.updated_at = _now_iso()
            import json
            job_id = str(uuid.uuid4())
            with self._storage.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR REPLACE INTO approvals VALUES (?,?)", (approval_id, updated.model_dump_json()))
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (job_id, "remediation", approval_id, json.dumps({"approval_id": approval_id}), "pending", _now_iso(), _now_iso(), None))
            self._plans[approval_id] = updated
            return updated, job_id

    def execute_approved(self, approval_id: str, *, on_update=None) -> RemediationPlan:
        with self._lock:
            plan = self._require_plan(approval_id)
            if plan.status != "APPROVED":
                raise ValueError("Remediation is not approved for execution")
        return self.approve_and_execute(approval_id, approver=plan.approved_by or "", reason=plan.approval_reason or "", on_update=on_update)

    def list(self, status: str | None = None) -> list[RemediationPlan]:
        with self._lock:
            plans = list(self._plans.values())
        if status:
            status_upper = status.upper()
            plans = [plan for plan in plans if plan.status.upper() == status_upper]
        return sorted(plans, key=lambda plan: plan.created_at, reverse=True)

    def get(self, approval_id: str) -> RemediationPlan | None:
        with self._lock:
            return self._plans.get(approval_id)

    def register_executor(self, agent_type: str, executor: Callable[[RemediationPlan], dict[str, Any]]) -> None:
        with self._lock:
            self._executors[agent_type] = executor

    def approve_and_execute(
        self,
        approval_id: str,
        *,
        approver: str,
        reason: str = "",
        on_update: Callable[[RemediationPlan], None] | None = None,
    ) -> RemediationPlan:
        with self._lock:
            plan = self._require_plan(approval_id)
            if plan.status not in {"WAITING_FOR_APPROVAL", "APPROVED"}:
                raise ValueError(f"Plan {approval_id} is not approvable in status {plan.status}")
            plan.status = "APPROVED"
            plan.approved_by = approver
            plan.approval_reason = reason
            plan.updated_at = _now_iso()
            executor = self._executors.get(plan.agent_type)
            self._persist(plan)

        if on_update:
            on_update(plan)

        if executor is None:
            with self._lock:
                plan.status = "APPROVED_NO_EXECUTOR"
                plan.updated_at = _now_iso()
                self._persist(plan)
            if on_update:
                on_update(plan)
            return plan

        try:
            result = executor(plan)
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif not isinstance(result, dict):
                result = {"result": result}
            with self._lock:
                plan.execution_result = result
                reported_status = str(result.get("status") or "").strip()
                execution_failed = result.get("success") is False or bool(result.get("error"))
                plan.status = reported_status or ("EXECUTION_FAILED" if execution_failed else "EXECUTED")
                plan.updated_at = _now_iso()
                self._persist(plan)
            if on_update:
                on_update(plan)
            return plan
        except Exception as exc:
            with self._lock:
                plan.execution_result = {"status": "FAILED", "error": type(exc).__name__, "message": str(exc)}
                plan.status = "EXECUTION_FAILED"
                plan.updated_at = _now_iso()
                self._persist(plan)
            if on_update:
                on_update(plan)
            raise

    def reject(self, approval_id: str, *, approver: str, reason: str = "") -> RemediationPlan:
        with self._lock:
            plan = self._require_plan(approval_id)
            if plan.status != "WAITING_FOR_APPROVAL":
                raise ValueError(f"Plan {approval_id} is not rejectable in status {plan.status}")
            plan.status = "REJECTED"
            plan.rejected_by = approver
            plan.rejection_reason = reason
            plan.updated_at = _now_iso()
            self._persist(plan)
            return plan

    def _require_plan(self, approval_id: str) -> RemediationPlan:
        plan = self._plans.get(approval_id)
        if plan is None:
            raise KeyError(approval_id)
        return plan

 
approval_store = ApprovalStore(job_store)
