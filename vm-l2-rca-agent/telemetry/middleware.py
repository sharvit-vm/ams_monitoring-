import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from telemetry.tracer import set_trace_id, get_trace_id
from telemetry.logger import tlog


class TelemetryMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):

        trace_id = str(uuid.uuid4())
        set_trace_id(trace_id)

        start = time.perf_counter()

        tlog("REQUEST_RECEIVED", event="REQUEST_RECEIVED", status="STARTED", extra={
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else "unknown"
        })

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 3)

            tlog("REQUEST_COMPLETED", event="REQUEST_COMPLETED", status="SUCCESS", extra={
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "path": request.url.path
            })

            response.headers["X-Trace-Id"] = trace_id
            return response

        except Exception as ex:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            tlog("REQUEST_FAILED", event="REQUEST_FAILED", status="ERROR", extra={
                "error": str(ex),
                "duration_ms": duration_ms,
                "path": request.url.path
            })
            raise
