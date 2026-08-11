from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field


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
    """Thread-safe in-memory approval store for one-service deployments.

    This keeps Render/demo behavior simple. For multi-instance production, replace
    this store with DynamoDB/Postgres while preserving the same method contract.
    """

    def __init__(self) -> None:
        self._plans: dict[str, RemediationPlan] = {}
        self._executors: dict[str, Callable[[RemediationPlan], dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def create(self, plan: RemediationPlan) -> RemediationPlan:
        with self._lock:
            plan.status = plan.status or "WAITING_FOR_APPROVAL"
            plan.updated_at = _now_iso()
            self._plans[plan.approval_id] = plan
            return plan

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

    def approve_and_execute(self, approval_id: str, *, approver: str, reason: str = "") -> RemediationPlan:
        with self._lock:
            plan = self._require_plan(approval_id)
            if plan.status not in {"WAITING_FOR_APPROVAL", "APPROVED"}:
                raise ValueError(f"Plan {approval_id} is not approvable in status {plan.status}")
            plan.status = "APPROVED"
            plan.approved_by = approver
            plan.approval_reason = reason
            plan.updated_at = _now_iso()
            executor = self._executors.get(plan.agent_type)

        if executor is None:
            with self._lock:
                plan.status = "APPROVED_NO_EXECUTOR"
                plan.updated_at = _now_iso()
            return plan

        try:
            result = executor(plan)
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif not isinstance(result, dict):
                result = {"result": result}
            with self._lock:
                plan.execution_result = result
                plan.status = str(result.get("status") or "EXECUTED")
                plan.updated_at = _now_iso()
            return plan
        except Exception as exc:
            with self._lock:
                plan.execution_result = {"status": "FAILED", "error": type(exc).__name__, "message": str(exc)}
                plan.status = "EXECUTION_FAILED"
                plan.updated_at = _now_iso()
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
            return plan

    def _require_plan(self, approval_id: str) -> RemediationPlan:
        plan = self._plans.get(approval_id)
        if plan is None:
            raise KeyError(approval_id)
        return plan


approval_store = ApprovalStore()
