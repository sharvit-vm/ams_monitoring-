"""
services/explanation_service.py
────────────────────────────────────────────────────────────────────────────
ExplanationService — generates a dynamic, human-readable remediation
explanation from actual pipeline execution data.

Responsibilities (Single Responsibility Principle):
  - Accept all runtime artefacts produced by the pipeline.
  - Determine the overall remediation scenario.
  - Build a structured explanation dict with a narrative summary.
  - Never use hardcoded or static explanation strings.

Scenarios handled:
  - NO_ISSUES_FOUND          : health check passed, nothing to remediate
  - FULL_SUCCESS             : all actions succeeded, verification passed
  - PARTIAL_SUCCESS          : some actions succeeded, verification passed
  - VERIFICATION_FAILED      : actions ran but post-check still unhealthy
  - REMEDIATION_FAILED       : all automated actions failed
  - MANUAL_INTERVENTION      : non-automated actions present, cannot auto-fix
"""

import time
from utils.logger import logger


class ExplanationService:
    """Generates a dynamic remediation explanation from pipeline runtime data."""

    def generate(
        self,
        *,
        initial_health: dict,
        issues: list[dict],
        actions: list[dict],
        execution: list[dict],
        verification: dict,
        final_health: dict,
        total_elapsed_ms: float,
        ctx: dict | None = None,
    ) -> dict:
        """
        Build and return the explanation dict.

        Parameters
        ----------
        initial_health    : health snapshot before remediation
        issues            : diagnosed issues list
        actions           : remediation plan (one entry per issue)
        execution         : execution results (one entry per action)
        verification      : post-remediation health snapshot
        final_health      : same as verification (alias for clarity)
        total_elapsed_ms  : total pipeline wall-clock time
        ctx               : logging context dict

        Returns
        -------
        dict with keys: scenario, summary, narrative, overall_status,
                        initial_status, final_status, issues_summary,
                        actions_summary, verification_summary,
                        total_elapsed_ms
        """
        ticket = (ctx or {}).get("ticket_id", "N/A")

        logger.info(
            f"[svc=DB-FIX-AGENT] [ticket={ticket}] "
            f"[step=EXPLANATION] Generating remediation explanation"
        )
        t0 = time.perf_counter()

        scenario        = self._determine_scenario(issues, actions, execution, verification)
        initial_status  = initial_health.get("status", "UNKNOWN")
        final_status    = verification.get("status", "UNKNOWN")
        issues_summary  = self._build_issues_summary(issues)
        actions_summary = self._build_actions_summary(actions, execution)
        verif_summary   = self._build_verification_summary(verification)
        narrative       = self._build_narrative(
            scenario, initial_health, issues, actions, execution,
            verification, total_elapsed_ms,
        )
        summary         = self._build_summary(scenario, issues, actions, execution, final_status)

        explanation = {
            "scenario":             scenario,
            "summary":              summary,
            "narrative":            narrative,
            "overall_status":       final_status,
            "initial_status":       initial_status,
            "final_status":         final_status,
            "issues_summary":       issues_summary,
            "actions_summary":      actions_summary,
            "verification_summary": verif_summary,
            "total_elapsed_ms":     round(total_elapsed_ms, 2),
        }

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[svc=DB-FIX-AGENT] [ticket={ticket}] "
            f"[step=EXPLANATION] Explanation generated successfully  "
            f"scenario={scenario}  summary_length={len(summary)}  "
            f"elapsed={elapsed:.1f}ms"
        )

        return explanation

    # ── Scenario determination ────────────────────────────────────────────────

    def _determine_scenario(
        self,
        issues: list[dict],
        actions: list[dict],
        execution: list[dict],
        verification: dict,
    ) -> str:
        if not issues:
            return "NO_ISSUES_FOUND"

        verification_passed = verification.get("status") == "HEALTHY"
        results = [r.get("result", "UNKNOWN") for r in execution]
        succeeded = sum(1 for r in results if r == "SUCCESS")
        failed    = sum(1 for r in results if r not in ("SUCCESS", "SKIPPED"))
        manual    = sum(1 for a in actions if not a.get("automated", True))

        if manual > 0 and succeeded == 0:
            return "MANUAL_INTERVENTION"
        if failed > 0 and succeeded == 0:
            return "REMEDIATION_FAILED"
        if not verification_passed:
            return "VERIFICATION_FAILED"
        if failed > 0 or manual > 0:
            return "PARTIAL_SUCCESS"
        return "FULL_SUCCESS"

    # ── Summary (one-liner for API response / RCA report) ─────────────────────

    def _build_summary(
        self,
        scenario: str,
        issues: list[dict],
        actions: list[dict],
        execution: list[dict],
        final_status: str,
    ) -> str:
        results   = [r.get("result", "UNKNOWN") for r in execution]
        succeeded = sum(1 for r in results if r == "SUCCESS")
        failed    = sum(1 for r in results if r not in ("SUCCESS", "SKIPPED"))

        summaries = {
            "NO_ISSUES_FOUND":       "Database health check passed. No issues detected; no remediation required.",
            "FULL_SUCCESS":          (
                f"All {len(issues)} issue(s) remediated successfully. "
                f"{succeeded} action(s) executed. "
                f"Post-remediation verification PASSED. Database status: {final_status}."
            ),
            "PARTIAL_SUCCESS":       (
                f"{succeeded} of {len(actions)} action(s) succeeded; "
                f"{failed} failed or required manual intervention. "
                f"Post-remediation verification PASSED. Database status: {final_status}."
            ),
            "VERIFICATION_FAILED":   (
                f"{succeeded} action(s) executed but post-remediation verification FAILED. "
                f"Database status remains {final_status}. Manual review recommended."
            ),
            "REMEDIATION_FAILED":    (
                f"Remediation attempted for {len(issues)} issue(s) but all {failed} action(s) failed. "
                f"Database status: {final_status}. Immediate manual intervention required."
            ),
            "MANUAL_INTERVENTION":   (
                f"{len(issues)} issue(s) detected requiring manual intervention. "
                f"No automated actions could be applied. Database status: {final_status}."
            ),
        }
        return summaries.get(scenario, f"Remediation completed with scenario: {scenario}.")

    # ── Narrative (multi-line for work notes / RCA report) ───────────────────

    def _build_narrative(
        self,
        scenario: str,
        initial_health: dict,
        issues: list[dict],
        actions: list[dict],
        execution: list[dict],
        verification: dict,
        total_elapsed_ms: float,
    ) -> str:
        lines: list[str] = []

        # 1. Initial health
        lines.append(
            f"Initial database status was {initial_health.get('status', 'UNKNOWN')} "
            f"(CPU: {initial_health.get('cpu_usage', 'N/A')}%, "
            f"Memory: {initial_health.get('memory_usage', 'N/A')}%, "
            f"Connections: {initial_health.get('active_connections', 'N/A')}/{initial_health.get('max_connections', 'N/A')}, "
            f"Slow queries: {initial_health.get('slow_queries', 'N/A')}, "
            f"Deadlocks: {initial_health.get('deadlocks', 'N/A')})."
        )

        # 2. Issues
        if not issues:
            lines.append("Automated diagnosis found no issues. The database was operating within healthy thresholds.")
        else:
            issue_list = ", ".join(
                f"{i['issue']} [{i.get('severity', 'UNKNOWN')}]" for i in issues
            )
            lines.append(
                f"Automated diagnosis identified {len(issues)} issue(s): {issue_list}."
            )

        # 3. Actions and results
        if actions:
            for action, result in zip(actions, execution):
                result_str = result.get("result", "UNKNOWN")
                automated  = "automated" if action.get("automated", True) else "manual"
                lines.append(
                    f"Action '{action['action']}' ({automated}) for issue "
                    f"'{action['issue']}': {result_str}."
                )
        else:
            lines.append("No remediation actions were generated.")

        # 4. Verification
        verif_status = verification.get("status", "UNKNOWN")
        lines.append(
            f"Post-remediation verification: database status is {verif_status} "
            f"(CPU: {verification.get('cpu_usage', 'N/A')}%, "
            f"Memory: {verification.get('memory_usage', 'N/A')}%, "
            f"Connections: {verification.get('active_connections', 'N/A')}, "
            f"Slow queries: {verification.get('slow_queries', 'N/A')}, "
            f"Deadlocks: {verification.get('deadlocks', 'N/A')})."
        )

        # 5. Outcome sentence
        outcome_map = {
            "NO_ISSUES_FOUND":     "No action was required; the database remains healthy.",
            "FULL_SUCCESS":        "All issues were resolved autonomously. The database has returned to a healthy state.",
            "PARTIAL_SUCCESS":     "Partial remediation was achieved. Some issues may require follow-up.",
            "VERIFICATION_FAILED": "Remediation actions were applied but the database did not return to a healthy state. Manual investigation is required.",
            "REMEDIATION_FAILED":  "All automated remediation actions failed. Immediate manual intervention is required.",
            "MANUAL_INTERVENTION": "The detected issues cannot be resolved autonomously. Manual intervention is required.",
        }
        lines.append(outcome_map.get(scenario, "Remediation pipeline completed."))
        lines.append(f"Total pipeline execution time: {total_elapsed_ms:.0f}ms.")

        return " ".join(lines)

    # ── Structured sub-summaries ──────────────────────────────────────────────

    def _build_issues_summary(self, issues: list[dict]) -> list[dict]:
        return [
            {"issue": i["issue"], "severity": i.get("severity", "UNKNOWN")}
            for i in issues
        ]

    def _build_actions_summary(self, actions: list[dict], execution: list[dict]) -> list[dict]:
        return [
            {
                "action":    a["action"],
                "issue":     a["issue"],
                "automated": a.get("automated", True),
                "result":    r.get("result", "UNKNOWN"),
            }
            for a, r in zip(actions, execution)
        ]

    def _build_verification_summary(self, verification: dict) -> dict:
        return {
            "status":             verification.get("status", "UNKNOWN"),
            "passed":             verification.get("status") == "HEALTHY",
            "cpu_usage":          verification.get("cpu_usage"),
            "memory_usage":       verification.get("memory_usage"),
            "active_connections": verification.get("active_connections"),
            "slow_queries":       verification.get("slow_queries"),
            "deadlocks":          verification.get("deadlocks"),
        }
