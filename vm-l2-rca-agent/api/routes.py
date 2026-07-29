import traceback
from fastapi import APIRouter, HTTPException

from agents.l2_rca_agent import L2RCAAgent
from models.api_response import APIResponse
from models.request import IncidentRequest
from utils.exceptions import DiagnosisException
from telemetry.logger import tlog
from telemetry.tracer import get_trace_id

router = APIRouter()
agent = L2RCAAgent()


@router.post("/analyze", response_model=APIResponse)
def analyze_incident(request: IncidentRequest):

    try:
        return agent.analyze(request)

    except DiagnosisException as ex:
        tlog("DIAGNOSIS_FAILED", event="DIAGNOSIS_FAILED", status="ERROR",
             extra={
                 "trace_id": get_trace_id(),
                 "ticket_id": request.ticket_id,
                 "error": str(ex),
                 "stack_trace": traceback.format_exc(),
                 "failure_step": "DiagnosisService"
             })
        raise HTTPException(status_code=500, detail=str(ex))

    except Exception as ex:
        tlog("UNHANDLED_ERROR", event="UNHANDLED_ERROR", status="ERROR",
             extra={
                 "trace_id": get_trace_id(),
                 "ticket_id": request.ticket_id,
                 "error": str(ex),
                 "stack_trace": traceback.format_exc(),
                 "failure_step": "Unknown"
             })
        raise HTTPException(status_code=500, detail="Internal server error")