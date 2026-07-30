"""
telemetry/structured_logger.py
────────────────────────────────────────────────────────────────────────────
Emits machine-readable JSON telemetry events.

Design
──────
• Uses a SEPARATE Python logger ("DBFixAgent.telemetry") so JSON events
  never pollute the human-readable execution log stream.
• Every event carries the full trace context so it can be ingested by
  any log aggregator (CloudWatch, Datadog, Elastic, Loki, etc.) and
  correlated with the human logs by trace_id / ticket_id.
• Callers never build JSON manually — they call typed helper functions.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from decimal import Decimal
from datetime import date, datetime

from db_fix.telemetry.tracer import TraceContext

# ── Dedicated telemetry logger ────────────────────────────────────────────────
# Writes JSON to the same stdout stream Render captures, but on its own
# logger name so it can be filtered / routed independently.
_tlog = logging.getLogger("DBFixAgent.telemetry")

AGENT = "DB Fix Agent"

def _json_serializer(obj):
    """
    Serialize objects that Python's json module doesn't support by default.
    """

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    return str(obj)


# ── Core emitter ──────────────────────────────────────────────────────────────

def _emit(trace: TraceContext, event: str, status: str,
          step: Optional[str] = None,
          latency_ms: Optional[float] = None,
          **extra: Any) -> None:
    """Build and emit a single structured JSON telemetry event."""
    payload: dict = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "agent":          AGENT,
        "event":          event,
        "status":         status,
        "trace_id":       trace.trace_id,
        "ticket_id":      trace.ticket_id,
        "correlation_id": trace.correlation_id,
        "request_id":     trace.request_id,
    }
    if step:
        payload["step"] = step
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 2)
    payload.update(extra)
    _tlog.info(
        json.dumps(
            payload,
            default=_json_serializer,
            ensure_ascii=False
        )
    )


# ── Pipeline lifecycle events ─────────────────────────────────────────────────

def emit_request_received(trace: TraceContext, application: str,
                          technology: str, problem_domain: str) -> None:
    _emit(trace, "REQUEST_RECEIVED", "INFO",
          step="PIPELINE_START",
          application=application,
          technology=technology,
          problem_domain=problem_domain)


def emit_pipeline_completed(trace: TraceContext, latency_ms: float,
                             issues_found: int, actions_executed: int,
                             initial_status: str, final_status: str,
                             verification: str, confidence: float) -> None:
    _emit(trace, "PIPELINE_COMPLETED", "SUCCESS",
          step="PIPELINE_END",
          latency_ms=latency_ms,
          issues_found=issues_found,
          actions_executed=actions_executed,
          initial_status=initial_status,
          final_status=final_status,
          verification=verification,
          confidence=confidence)


def emit_pipeline_failed(trace: TraceContext, latency_ms: float,
                         error_type: str, error_message: str,
                         step: str) -> None:
    _emit(trace, "PIPELINE_FAILED", "ERROR",
          step=step,
          latency_ms=latency_ms,
          error_type=error_type,
          error_message=error_message,
          recovery_attempt=False,
          failure_category="AGENT_EXECUTION_ERROR")


# ── Database telemetry events ─────────────────────────────────────────────────

def emit_db_connecting(trace: TraceContext, operation: str,
                       host_masked: str, db_name: str) -> None:
    _emit(trace, "DATABASE_CONNECTING", "INFO",
          step="DB_CONNECTION",
          operation=operation,
          host=host_masked,
          database=db_name,
          ssl="enabled")


def emit_db_connected(trace: TraceContext, operation: str,
                      latency_ms: float) -> None:
    _emit(trace, "DATABASE_CONNECTED", "SUCCESS",
          step="DB_CONNECTION",
          operation=operation,
          latency_ms=latency_ms)


def emit_db_connection_failed(trace: TraceContext, operation: str,
                               latency_ms: float, error: str) -> None:
    _emit(trace, "DATABASE_CONNECTION_FAILED", "ERROR",
          step="DB_CONNECTION",
          operation=operation,
          latency_ms=latency_ms,
          error=error)


def emit_query_started(trace: TraceContext, operation: str) -> None:
    _emit(trace, "QUERY_STARTED", "INFO",
          step="DB_QUERY",
          operation=operation)


def emit_query_completed(trace: TraceContext, operation: str,
                         rows: int, latency_ms: float) -> None:
    _emit(trace, "QUERY_COMPLETED", "SUCCESS",
          step="DB_QUERY",
          operation=operation,
          rows_returned=rows,
          latency_ms=latency_ms)


def emit_query_failed(trace: TraceContext, operation: str,
                      latency_ms: float, error: str) -> None:
    _emit(trace, "QUERY_FAILED", "ERROR",
          step="DB_QUERY",
          operation=operation,
          latency_ms=latency_ms,
          error=error)


# ── Domain events ─────────────────────────────────────────────────────────────

def emit_health_retrieved(trace: TraceContext, latency_ms: float,
                          status: str, connections: int,
                          cpu: float, memory: float,
                          slow_queries: int, deadlocks: int) -> None:
    _emit(trace, "DATABASE_HEALTH_RETRIEVED", "SUCCESS",
          step="READ_DATABASE_HEALTH",
          latency_ms=latency_ms,
          db_status=status,
          active_connections=connections,
          cpu_usage=cpu,
          memory_usage=memory,
          slow_queries=slow_queries,
          deadlocks=deadlocks)


def emit_diagnosis_completed(trace: TraceContext, latency_ms: float,
                              issue_count: int, issues: list) -> None:
    _emit(trace, "DIAGNOSIS_COMPLETED", "SUCCESS",
          step="DIAGNOSIS",
          latency_ms=latency_ms,
          issue_count=issue_count,
          issues=[i["issue"] for i in issues],
          severities={i["issue"]: i["severity"] for i in issues})


def emit_remediation_plan_created(trace: TraceContext, latency_ms: float,
                                   action_count: int, actions: list) -> None:
    _emit(trace, "REMEDIATION_PLAN_CREATED", "SUCCESS",
          step="GENERATE_REMEDIATION_PLAN",
          latency_ms=latency_ms,
          action_count=action_count,
          actions=[a["action"] for a in actions])


def emit_remediation_started(trace: TraceContext, action_count: int) -> None:
    _emit(trace, "REMEDIATION_STARTED", "INFO",
          step="EXECUTE_REMEDIATION",
          action_count=action_count)


def emit_action_executed(trace: TraceContext, index: int, total: int,
                          action: str, issue: str,
                          result: str, latency_ms: float) -> None:
    status = "SUCCESS" if result == "SUCCESS" else "FAILED"
    _emit(trace, "ACTION_EXECUTED", status,
          step="EXECUTE_REMEDIATION",
          action_index=index,
          action_total=total,
          action=action,
          issue=issue,
          result=result,
          latency_ms=latency_ms)


def emit_verification_completed(trace: TraceContext, latency_ms: float,
                                  status: str, passed: bool,
                                  connections: int, cpu: float,
                                  memory: float, deadlocks: int,
                                  slow_queries: int) -> None:
    _emit(trace, "DATABASE_HEALTH_VERIFIED", "SUCCESS" if passed else "FAILED",
          step="VERIFICATION",
          latency_ms=latency_ms,
          db_status=status,
          verification_passed=passed,
          active_connections=connections,
          cpu_usage=cpu,
          memory_usage=memory,
          deadlocks=deadlocks,
          slow_queries=slow_queries)


def emit_token_usage(trace: TraceContext, token_dict: dict) -> None:
    _emit(trace, "TOKEN_USAGE_RECORDED", "INFO",
          step="PIPELINE_END",
          token_usage=token_dict)


def emit_response_returned(trace: TraceContext, latency_ms: float) -> None:
    _emit(trace, "RESPONSE_RETURNED", "SUCCESS",
          step="PIPELINE_END",
          latency_ms=latency_ms)
