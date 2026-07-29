"""
telemetry/explainability.py
────────────────────────────────────────────────────────────────────────────
Builds structured explainability objects for every remediation decision.

For each diagnosed issue the engine produces:
  - issue / severity
  - evidence (observed values)
  - threshold (the rule that triggered the diagnosis)
  - why_problem (human-readable explanation)
  - selected_action / why_selected
  - expected_outcome
  - confidence

The final explanation object is embedded in the API response so
consumers (L2 RCA Agent, dashboards, audit systems) understand WHY
the agent took each action without reading source code.

No business logic lives here — this module only interprets the data
that DiagnosisService and RemediationService already produced.
"""

from typing import Any, Optional


# ── Per-issue knowledge base ──────────────────────────────────────────────────
# Maps issue_code → static explainability metadata.
# Thresholds mirror DiagnosisService exactly — update both together.

_ISSUE_KNOWLEDGE: dict[str, dict] = {
    "CONNECTION_POOL_EXHAUSTED": {
        "threshold":        "active_connections >= max_connections",
        "threshold_pct":    100,
        "why_problem":      (
            "The connection pool is fully exhausted. No new client sessions "
            "can be established, causing application timeouts and service degradation."
        ),
        "why_selected":     (
            "Terminating idle sessions immediately frees connection slots "
            "without restarting the database, minimising downtime."
        ),
        "expected_outcome": (
            "Active connections drop below 80% of max_connections, "
            "restoring the ability to accept new sessions."
        ),
        "confidence":       0.98,
        "alternatives":     [
            "Increase max_connections (requires restart)",
            "Enable PgBouncer connection pooling",
            "Scale application horizontally to reduce per-instance connections",
        ],
    },
    "HIGH_CPU": {
        "threshold":        "cpu_usage >= 90%",
        "threshold_pct":    90,
        "why_problem":      (
            "CPU utilisation has exceeded 90%. Query execution is degraded, "
            "response times are elevated, and the risk of cascading failures is high."
        ),
        "why_selected":     (
            "Killing long-running queries releases CPU immediately. "
            "This is the safest automated action before escalating to manual tuning."
        ),
        "expected_outcome": (
            "CPU utilisation drops below 70% within 60 seconds of "
            "terminating the offending queries."
        ),
        "confidence":       0.91,
        "alternatives":     [
            "Add missing indexes on hot tables",
            "Rewrite expensive queries",
            "Scale up compute tier",
        ],
    },
    "HIGH_MEMORY": {
        "threshold":        "memory_usage >= 90%",
        "threshold_pct":    90,
        "why_problem":      (
            "Memory usage has exceeded 90%. The risk of OOM-killer termination "
            "and shared_buffers eviction is significant."
        ),
        "why_selected":     (
            "Clearing the query plan cache and releasing idle backend memory "
            "is the fastest non-destructive action available."
        ),
        "expected_outcome": (
            "Memory usage drops below 75% after cache flush and "
            "idle backend termination."
        ),
        "confidence":       0.88,
        "alternatives":     [
            "Reduce work_mem per session",
            "Tune shared_buffers",
            "Scale up memory tier",
        ],
    },
    "SLOW_QUERIES": {
        "threshold":        "slow_queries > 10",
        "threshold_pct":    None,
        "why_problem":      (
            "More than 10 slow queries are active simultaneously. "
            "These consume disproportionate CPU and I/O, degrading all other workloads."
        ),
        "why_selected":     (
            "Cancelling queries exceeding the slow-query threshold stops "
            "resource contention without data loss."
        ),
        "expected_outcome": (
            "Slow query count drops to 0 and overall query latency "
            "returns to baseline within 30 seconds."
        ),
        "confidence":       0.85,
        "alternatives":     [
            "Add pg_stat_statements analysis",
            "Create covering indexes",
            "Partition large tables",
        ],
    },
    "DEADLOCKS": {
        "threshold":        "deadlocks > 0",
        "threshold_pct":    None,
        "why_problem":      (
            "Active deadlocks detected. Transactions are blocking each other "
            "indefinitely, causing application hangs and data inconsistency risk."
        ),
        "why_selected":     (
            "PostgreSQL automatically resolves deadlocks by rolling back one "
            "transaction. The remediation ensures all deadlock victims are "
            "cleanly rolled back and their connections released."
        ),
        "expected_outcome": (
            "Deadlock count drops to 0 and blocked transactions are released."
        ),
        "confidence":       0.94,
        "alternatives":     [
            "Refactor application transaction ordering",
            "Reduce transaction scope",
            "Add advisory locks",
        ],
    },
}

_DEFAULT_KNOWLEDGE: dict = {
    "threshold":        "custom rule",
    "threshold_pct":    None,
    "why_problem":      "Issue detected by automated diagnosis.",
    "why_selected":     "Best available automated remediation rule.",
    "expected_outcome": "Issue resolved after remediation.",
    "confidence":       0.75,
    "alternatives":     [],
}


# ── Evidence builder ──────────────────────────────────────────────────────────

def _build_evidence(issue_code: str, health: dict) -> list[dict]:
    """Extract the specific health metrics that triggered this issue."""
    evidence_map: dict[str, list[dict]] = {
        "CONNECTION_POOL_EXHAUSTED": [
            {"metric": "active_connections", "observed": health.get("active_connections"),
             "threshold": health.get("max_connections"), "unit": "connections"},
            {"metric": "utilisation_pct",
             "observed": round(
                 health.get("active_connections", 0) /
                 max(health.get("max_connections", 1), 1) * 100, 1
             ),
             "threshold": 100, "unit": "%"},
        ],
        "HIGH_CPU": [
            {"metric": "cpu_usage", "observed": health.get("cpu_usage"),
             "threshold": 90, "unit": "%"},
        ],
        "HIGH_MEMORY": [
            {"metric": "memory_usage", "observed": health.get("memory_usage"),
             "threshold": 90, "unit": "%"},
        ],
        "SLOW_QUERIES": [
            {"metric": "slow_queries", "observed": health.get("slow_queries"),
             "threshold": 10, "unit": "queries"},
        ],
        "DEADLOCKS": [
            {"metric": "deadlocks", "observed": health.get("deadlocks"),
             "threshold": 0, "unit": "deadlocks"},
        ],
    }
    return evidence_map.get(issue_code, [
        {"metric": issue_code, "observed": "detected", "threshold": "rule", "unit": "N/A"}
    ])


# ── Public API ────────────────────────────────────────────────────────────────

def build_issue_explanation(
    issue: dict,
    action: Optional[dict],
    health: dict,
) -> dict:
    """
    Build a full explanation object for a single diagnosed issue.

    Parameters
    ----------
    issue  : {"issue": str, "severity": str}
    action : {"issue": str, "action": str, "automated": bool} or None
    health : raw database_health row
    """
    code = issue["issue"]
    kb   = _ISSUE_KNOWLEDGE.get(code, _DEFAULT_KNOWLEDGE)

    return {
        "issue":            code,
        "severity":         issue["severity"],
        "evidence":         _build_evidence(code, health),
        "threshold":        kb["threshold"],
        "why_problem":      kb["why_problem"],
        "selected_action":  action["action"] if action else "NO_RULE_FOUND",
        "why_selected":     kb["why_selected"] if action else "No matching remediation rule.",
        "expected_outcome": kb["expected_outcome"],
        "confidence":       kb["confidence"],
        "alternatives":     kb["alternatives"],
        "automated":        action["automated"] if action else False,
    }


def build_explanation(
    issues: list[dict],
    actions: list[dict],
    health: dict,
    verification_passed: bool,
    total_elapsed_ms: float,
) -> dict:
    """
    Build the top-level explanation object embedded in the API response.

    Parameters
    ----------
    issues              : list of diagnosed issues
    actions             : list of remediation actions
    health              : initial database_health snapshot
    verification_passed : whether post-remediation verification passed
    total_elapsed_ms    : total pipeline duration
    """
    # Map issue_code → action for quick lookup
    action_map: dict[str, dict] = {a["issue"]: a for a in actions}

    issue_explanations = [
        build_issue_explanation(issue, action_map.get(issue["issue"]), health)
        for issue in issues
    ]

    # Overall confidence = mean of individual confidences, or 1.0 if no issues
    if issue_explanations:
        overall_confidence = round(
            sum(e["confidence"] for e in issue_explanations) / len(issue_explanations), 4
        )
    else:
        overall_confidence = 1.0

    # Severity ranking for summary
    severities = [i["severity"] for i in issues]
    highest_severity = (
        "CRITICAL" if "CRITICAL" in severities else
        "HIGH"     if "HIGH"     in severities else
        "MEDIUM"   if "MEDIUM"   in severities else
        "LOW"      if "LOW"      in severities else
        "NONE"
    )

    if not issues:
        summary = "Database health check passed. No issues detected. No remediation required."
    else:
        action_names = [a["action"] for a in actions]
        summary = (
            f"Autonomous diagnosis identified {len(issues)} issue(s) "
            f"(highest severity: {highest_severity}). "
            f"Executed {len(actions)} remediation action(s): "
            f"{', '.join(action_names)}. "
            f"Post-remediation verification {'PASSED' if verification_passed else 'FAILED'}. "
            f"Total pipeline time: {total_elapsed_ms:.0f}ms."
        )

    return {
        "summary":            summary,
        "overall_confidence": overall_confidence,
        "highest_severity":   highest_severity,
        "issues_explained":   issue_explanations,
        "selected_actions":   [a["action"] for a in actions],
        "verification":       "PASSED" if verification_passed else "FAILED",
        "verification_passed": verification_passed,
        "expected_outcome":   (
            "All identified issues resolved. Database returned to HEALTHY state."
            if verification_passed
            else "Verification failed. Manual intervention may be required."
        ),
        "total_elapsed_ms":   round(total_elapsed_ms, 2),
    }
