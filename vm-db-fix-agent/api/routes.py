import traceback

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agents.db_fix_agent import DBFixAgent
from models.request import RCARequest
from utils.logger import logger


router = APIRouter(
    prefix="/api/v1",
    tags=["DB Fix Agent"]
)

agent = DBFixAgent()


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
                "ticket_id":  request.ticket_id,
                "request_id": request_id,
                "status":     "FAILED",
                "error":      type(e).__name__,
                "message":    str(e),
            }
        )