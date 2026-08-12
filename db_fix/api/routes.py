import traceback

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db_fix.agents.db_fix_agent import DBFixAgent
from db_fix.models.request import RCARequest
from db_fix.utils.logger import logger
from dashboard_state import latest_execution, record_execution
from governance.approvals import approval_store
from governance.telemetry import emit_governance_event


router = APIRouter(
    prefix="/api/v1",
    tags=["DB Fix Agent"]
)

health_router = APIRouter(tags=["Health"])
agent = DBFixAgent()


class ApprovalDecision(BaseModel):
    approver: str
    reason: str = ""


def _execute_db_fix_plan(plan) -> dict:
    request_payload = plan.execution_context.get("request", {})
    request = RCARequest(**request_payload)
    return agent.execute(
        request,
        request_id=plan.execution_context.get("request_id"),
        require_approval=False,
    )


approval_store.register_executor("db_fix", _execute_db_fix_plan)

def _record_remediation_dashboard_update(plan) -> None:
    current = latest_execution()
    if not current or not isinstance(current.get("response"), dict):
        return
    response = dict(current["response"])
    plan_payload = plan.model_dump()
    response["fix_agent"] = plan_payload
    if plan.execution_result:
        if plan.agent_type == "code_fix":
            existing_codefix = response.get("codefix") if isinstance(response.get("codefix"), dict) else {}
            response["codefix"] = {
                **existing_codefix,
                **plan.execution_result,
                "approval_id": plan.approval_id,
                "approval_status": plan.status,
            }
        else:
            response["db_fix"] = plan.execution_result
            l2_rca = response.get("l2_rca")
            if isinstance(l2_rca, dict):
                l2_response = l2_rca.get("response")
                if isinstance(l2_response, dict):
                    data = l2_response.setdefault("data", {})
                    if isinstance(data, dict):
                        data["execution"] = plan.execution_result
    response["status"] = plan.status
    record_execution(response)

@health_router.get("/health")
@health_router.head("/health")
def health():
    return {"status": "ok", "service": "DB Fix Agent"}


@router.post("/execute")
def execute(request: RCARequest, http_request: Request):
    request_id = getattr(http_request.state, "request_id", None)
    try:
        return agent.execute(request, request_id=request_id)
    except Exception as e:
        logger.error(
            f"[ticket={request.ticket_id}] Unhandled exception in /execute  "
            f"error={type(e).__name__}: {e}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "ticket_id": request.ticket_id,
                "request_id": request_id,
                "status": "FAILED",
                "error": type(e).__name__,
                "message": str(e),
            },
        )


@router.get("/remediations")
def list_remediation_plans(status: str | None = None):
    return {
        "status": "ok",
        "plans": [plan.model_dump() for plan in approval_store.list(status=status)],
    }


@router.get("/remediations/{approval_id}")
def get_remediation_plan(approval_id: str):
    plan = approval_store.get(approval_id)
    if plan is None:
        return JSONResponse(status_code=404, content={"status": "not_found", "approval_id": approval_id})
    return {"status": "ok", "remediation_plan": plan.model_dump()}


@router.post("/remediations/{approval_id}/approve")
def approve_remediation(approval_id: str, decision: ApprovalDecision):
    try:
        plan = approval_store.approve_and_execute(
            approval_id,
            approver=decision.approver,
            reason=decision.reason,
        )
        _record_remediation_dashboard_update(plan)
        return {"status": plan.status, "remediation_plan": plan.model_dump()}
    except KeyError:
        return JSONResponse(status_code=404, content={"status": "not_found", "approval_id": approval_id})
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "failed", "approval_id": approval_id, "message": str(e)})


@router.post("/remediations/{approval_id}/reject")
def reject_remediation(approval_id: str, decision: ApprovalDecision):
    try:
        plan = approval_store.reject(
            approval_id,
            approver=decision.approver,
            reason=decision.reason,
        )
        emit_governance_event(
            "originating_platform.update_requested",
            approval_id=approval_id,
            source_platform=plan.source_platform,
            issue_id=plan.issue_id,
            status="MANUAL_INTERVENTION_REQUIRED",
            reason=decision.reason,
        )
        _record_remediation_dashboard_update(plan)
        return {"status": plan.status, "remediation_plan": plan.model_dump()}
    except KeyError:
        return JSONResponse(status_code=404, content={"status": "not_found", "approval_id": approval_id})
