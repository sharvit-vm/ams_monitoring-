"""
telemetry/execution_logger.py
────────────────────────────────────────────────────────────────────────────
Builds the structured execution timeline and prints the pipeline summary.

The timeline is a JSON-serialisable list embedded in the API response
so a React dashboard can render a visual pipeline timeline without any
backend changes.

The pipeline summary is printed as a human-readable block that sits
alongside (not replacing) the existing STEP-based execution log.
"""

from typing import Optional
from utils.logger import logger, BANNER, DIVIDER


# ── Timeline builder ──────────────────────────────────────────────────────────

_TIMELINE_STEPS = [
    ("CMDB_LOOKUP",          "CMDB Lookup"),
    ("RELATIONSHIP_LOOKUP",  "Relationship Lookup"),
    ("POSTGRES_DISCOVERY",   "PostgreSQL Discovery"),
    ("READ_DATABASE_HEALTH", "Database Health Read"),
    ("DIAGNOSIS",            "Diagnosis"),
    ("REMEDIATION_PLAN",     "Remediation Rule Lookup"),
    ("EXECUTION",            "Execution"),
    ("VERIFICATION",         "Verification"),
    ("INCIDENT_HISTORY",     "Incident History"),
    ("SN_NOTIFICATION",      "SN Notification"),
]


def build_timeline(ctx_timeline: dict) -> list[dict]:
    """
    Convert the ctx["timeline"] dict (label → seconds) into the
    structured list format expected by the response model and dashboard.

    Example output:
        [
          {"step": "READ_DATABASE_HEALTH", "duration_ms": 1095},
          {"step": "DIAGNOSIS",            "duration_ms": 75},
          ...
        ]
    """
    result = []
    for step_key, ctx_label in _TIMELINE_STEPS:
        elapsed_s = ctx_timeline.get(ctx_label)
        result.append({
            "step":        step_key,
            "label":       ctx_label,
            "duration_ms": round(elapsed_s * 1000, 2) if elapsed_s is not None else None,
        })
    return result


# ── Pipeline summary human log ────────────────────────────────────────────────

def log_pipeline_summary(
    ticket_id: str,
    trace_id: str,
    correlation_id: str,
    database: str,
    issues: list,
    actions: list,
    actions_succeeded: int,
    actions_failed: int,
    verification_status: str,
    total_elapsed_ms: float,
    overall_status: str,
    confidence: float,
    token_usage: Optional[dict] = None,
) -> None:
    """
    Print the PIPELINE SUMMARY block.

    This is additive — it appears after the existing MISSION ACCOMPLISHED
    banner and before the response is returned.
    """
    logger.info(BANNER)
    logger.info(f"  {'PIPELINE SUMMARY':^76}")
    logger.info(BANNER)
    logger.info(f"  Ticket              : {ticket_id}")
    logger.info(f"  Trace ID            : {trace_id}")
    logger.info(f"  Correlation ID      : {correlation_id}")
    logger.info(f"  Database            : {database}")
    logger.info(DIVIDER)
    logger.info(f"  Problems Detected   : {len(issues)}")
    if issues:
        for i, issue in enumerate(issues, 1):
            logger.info(f"    [{i}] {issue['issue']:<35} Severity: {issue['severity']}")
    logger.info(DIVIDER)
    logger.info(f"  Actions Executed    : {len(actions)}")
    logger.info(f"  Actions Succeeded   : {actions_succeeded}")
    logger.info(f"  Actions Failed      : {actions_failed}")
    logger.info(DIVIDER)
    logger.info(f"  Verification Status : {verification_status}")
    logger.info(f"  Overall Status      : {overall_status}")
    logger.info(f"  Confidence          : {confidence * 100:.1f}%")
    logger.info(f"  Total Execution Time: {total_elapsed_ms:.0f}ms")
    logger.info(DIVIDER)
    if token_usage:
        tokens  = token_usage.get("tokens", {})
        cost    = token_usage.get("cost_usd", {})
        pricing = token_usage.get("pricing_model", {})
        logger.info(f"  TOKEN USAGE & COST")
        logger.info(DIVIDER)
        logger.info(f"  Model               : {token_usage.get('model', 'N/A')}")
        logger.info(f"  Input Tokens        : {tokens.get('input', 0):,}")
        logger.info(f"  Output Tokens       : {tokens.get('output', 0):,}")
        logger.info(f"  Total Tokens        : {tokens.get('total', 0):,}")
        logger.info(DIVIDER)
        logger.info(f"  Input Cost          : ${cost.get('input', 0):.6f} USD")
        logger.info(f"  Output Cost         : ${cost.get('output', 0):.6f} USD")
        logger.info(f"  Total Cost          : ${cost.get('total', 0):.6f} USD")
        logger.info(f"  Pricing             : ${pricing.get('input_per_1k_tokens', 0):.4f}/1K in  "
                    f"${pricing.get('output_per_1k_tokens', 0):.4f}/1K out")
        logger.info(f"  Estimation          : {pricing.get('estimation_method', 'N/A')}")
        logger.info(DIVIDER)
    logger.info(BANNER)
