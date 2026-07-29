"""
telemetry/middleware.py
────────────────────────────────────────────────────────────────────────────
FastAPI middleware that injects a request_id into every incoming request.

The request_id is:
  - Generated here if the caller did not supply X-Request-ID
  - Accepted from the X-Request-ID header if the upstream L2 RCA Agent
    sends one (enabling end-to-end request tracing across services)
  - Echoed back in the X-Request-ID response header
  - Stored in request.state.request_id for use by route handlers
"""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject and propagate X-Request-ID across the request lifecycle."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = (
            request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        request.state.request_id = request_id

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
