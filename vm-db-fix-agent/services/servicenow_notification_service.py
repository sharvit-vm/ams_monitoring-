"""
services/servicenow_notification_service.py
────────────────────────────────────────────────────────────────────────────
Sends the DB Fix Agent remediation result back to the originating
ServiceNow incident as a structured work note and resolution update.

What it does
────────────
1. Builds a human-readable work note summarising every action taken,
   the verification result, and the final database status.
2. PATCHes the incident record on the `incident` table with:
   - work_notes  : full remediation summary (visible in Activity log)
   - state       : 6 (Resolved) if verification passed, else unchanged
   - close_notes : resolution summary if verified healthy
3. Logs every step using the existing structured logger.

No business logic — only formats data already produced by the pipeline
and calls the ServiceNow REST API.
"""

import time
from datetime import datetime, timezone

from clients.servicenow_client import ServiceNowClient
from utils.logger import logger, log_step_failure, DIVIDER


class ServiceNowNotificationService:

    # ServiceNow incident state codes
    _STATE_RESOLVED = "6"
    _STATE_IN_PROGRESS = "2"

    def __init__(self):
        self.client = ServiceNowClient()

    def notify(
        self,
        ticket_id: str,
        application: str,
        database: str,
        issues: list,
        actions: list,
        execution: list,
        verification: dict,
        explanation: dict,
        total_elapsed_ms: float,
        ctx: dict = None,
    ) -> dict:
        """
        Find the incident by ticket_id and PATCH it with the remediation result.

        Returns the updated ServiceNow record, or an error dict if the
        notification fails (non-fatal — pipeline result is still returned).
        """
        step = "ServiceNowNotificationService.notify"
        t0   = time.perf_counter()

        try:
            # ── Step 1: Look up the incident sys_id by ticket number ──────────
            if ctx:
                logger.info(
                    f"[svc=DB-FIX-AGENT] [ticket={ticket_id}] "
                    f"[step=SN_NOTIFY] Looking up incident record  ticket_id={ticket_id}"
                )
            incidents = self.client.get(
                table="incident",
                query=f"number={ticket_id}",
                ctx=ctx
            )
            if not incidents:
                raise Exception(
                    f"Incident {ticket_id} not found in ServiceNow — cannot send notification."
                )
            sys_id = incidents[0]["sys_id"]

            # ── Step 2: Build work note ───────────────────────────────────────
            work_note = self._build_work_note(
                ticket_id=ticket_id,
                application=application,
                database=database,
                issues=issues,
                actions=actions,
                execution=execution,
                verification=verification,
                explanation=explanation,
                total_elapsed_ms=total_elapsed_ms,
            )

            # ── Step 3: Build PATCH payload ───────────────────────────────────
            verification_passed = verification.get("status") == "HEALTHY"
            patch_payload = {"work_notes": work_note}

            if verification_passed:
                patch_payload["state"]       = self._STATE_RESOLVED
                patch_payload["close_notes"] = (
                    f"Autonomous DB Fix Agent resolved the incident.\n"
                    f"Database '{database}' returned to HEALTHY status.\n"
                    f"Actions taken: {', '.join(a['action'] for a in actions)}.\n"
                    f"Verification: PASSED.\n"
                    f"Total remediation time: {total_elapsed_ms:.0f}ms."
                )

            # ── Step 4: PATCH the incident ────────────────────────────────────
            result = self.client.patch(
                table="incident",
                sys_id=sys_id,
                payload=patch_payload,
                ctx=ctx
            )

            elapsed = time.perf_counter() - t0
            if ctx:
                logger.info(
                    f"[svc=DB-FIX-AGENT] [ticket={ticket_id}] "
                    f"[step=SN_NOTIFY] [SUCCESS]  "
                    f"incident={ticket_id}  sys_id={sys_id}  "
                    f"state={'RESOLVED' if verification_passed else 'UPDATED'}  "
                    f"elapsed={elapsed * 1000:.0f}ms"
                )

            return {
                "notified":   True,
                "sys_id":     sys_id,
                "state":      "RESOLVED" if verification_passed else "UPDATED",
                "elapsed_ms": round(elapsed * 1000, 2),
            }

        except Exception as e:
            elapsed = time.perf_counter() - t0
            if ctx:
                log_step_failure(ctx, step, elapsed, e)
            # Non-fatal — return error info so the pipeline can still respond
            return {
                "notified":   False,
                "error":      str(e),
                "elapsed_ms": round(elapsed * 1000, 2),
            }

    # ── Work note builder ─────────────────────────────────────────────────────

    def _build_work_note(
        self,
        ticket_id: str,
        application: str,
        database: str,
        issues: list,
        actions: list,
        execution: list,
        verification: dict,
        explanation: dict,
        total_elapsed_ms: float,
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "=" * 70,
            "  AUTONOMOUS DB FIX AGENT — REMEDIATION REPORT",
            "=" * 70,
            f"  Ticket          : {ticket_id}",
            f"  Application     : {application}",
            f"  Database        : {database}",
            f"  Timestamp       : {now}",
            f"  Agent           : DB Fix Agent (Autonomous)",
            "-" * 70,
            "  ISSUES DETECTED",
            "-" * 70,
        ]

        if not issues:
            lines.append("  No issues detected. Database was healthy.")
        else:
            for i, issue in enumerate(issues, 1):
                lines.append(f"  [{i}] {issue['issue']:<35} Severity: {issue['severity']}")

        lines += [
            "-" * 70,
            "  REMEDIATION ACTIONS EXECUTED",
            "-" * 70,
        ]

        if not actions:
            lines.append("  No actions required.")
        else:
            for i, (action, result) in enumerate(zip(actions, execution), 1):
                status_icon = "SUCCESS" if result.get("result") == "SUCCESS" else result.get("result", "UNKNOWN")
                lines.append(
                    f"  [{i}] {action['action']:<45} Result: {status_icon}"
                )

        lines += [
            "-" * 70,
            "  POST-REMEDIATION VERIFICATION",
            "-" * 70,
            f"  Database Status : {verification.get('status', 'UNKNOWN')}",
            f"  Connections     : {verification.get('active_connections', 'N/A')}",
            f"  CPU Usage       : {verification.get('cpu_usage', 'N/A')}%",
            f"  Memory Usage    : {verification.get('memory_usage', 'N/A')}%",
            f"  Slow Queries    : {verification.get('slow_queries', 'N/A')}",
            f"  Deadlocks       : {verification.get('deadlocks', 'N/A')}",
            f"  Verification    : {'PASSED' if verification.get('status') == 'HEALTHY' else 'FAILED'}",
            "-" * 70,
            "  SUMMARY",
            "-" * 70,
            f"  {explanation.get('summary', 'Remediation completed.')}",
            f"  Confidence      : {explanation.get('overall_confidence', 0) * 100:.1f}%",
            f"  Total Time      : {total_elapsed_ms:.0f}ms",
            "=" * 70,
        ]

        return "\n".join(lines)
