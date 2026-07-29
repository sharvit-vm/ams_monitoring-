"""
models/response.py
────────────────────────────────────────────────────────────────────────────
Enriched API response model.

Every field is designed so a React dashboard can consume it directly
without backend modifications.  The existing fields (ticket_id, database,
issues_found, actions, execution, verification) are preserved verbatim
for backward compatibility with the L2 RCA Agent.
"""

from typing import Any, List, Optional
from pydantic import BaseModel


class AgentResponse(BaseModel):
    # ── Traceability ──────────────────────────────────────────────────────────
    trace_id:       str
    ticket_id:      str
    correlation_id: str
    request_id:     str
    agent:          str = "DB Fix Agent"
    timestamp:      str

    # ── Outcome ───────────────────────────────────────────────────────────────
    status:         str   # SUCCESS | FAILED
    overall_status: str   # HEALTHY | DEGRADED | FAILED

    # ── Backward-compatible core fields ──────────────────────────────────────
    database:       str
    issues_found:   List[Any]
    actions:        List[Any]
    execution:      List[Any]
    verification:   dict

    # ── Telemetry ─────────────────────────────────────────────────────────────
    metrics:        dict
    timeline:       List[dict]

    # ── Explainability ────────────────────────────────────────────────────────
    explanation:    dict

    # ── Error (populated only on failure) ────────────────────────────────────
    error:          Optional[dict] = None
