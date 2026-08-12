"""
agents/db_fix_agent.py
────────────────────────────────────────────────────────────────────────────
DB Fix Agent — Autonomous Database Remediation Pipeline.

Workflow
────────
  Application Resolution → Guardrails → Diagnosis → Remediation Plan
  → Human Approval → Execute Fix → Verification → Response Builder
  → Notification

Application resolution queries the Enterprise Operations Database
(applications table) directly using the application name from the
incident request — no CMDB dependency.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from db_fix.models.request import RCARequest
from db_fix.services.application_resolution_service import ApplicationResolutionService
from db_fix.services.database_health_service import DatabaseHealthService
from db_fix.services.diagnosis_service import DiagnosisService
from db_fix.services.remediation_service import RemediationService
from db_fix.services.execution_service import ExecutionService
from db_fix.services.verification_service import VerificationService
from db_fix.services.incident_history_service import IncidentHistoryService
from db_fix.services.servicenow_notification_service import ServiceNowNotificationService
from db_fix.services.explanation_service import ExplanationService
from db_fix.utils.logger import (
    bind_context,
    log_start_banner, log_final_summary, log_failure_banner,
    log_step_header, log_info,
    log_health_summary, log_diagnosis_summary,
    log_verification_result, log_execution_timeline,
    record_timing, DIVIDER,
    logger,
)
from db_fix.telemetry.tracer          import new_trace, TraceContext
from db_fix.telemetry.metrics         import PipelineMetrics
from db_fix.telemetry.token_cost      import TokenUsage
from db_fix.telemetry.structured_logger import (
    emit_request_received,
    emit_pipeline_completed,
    emit_pipeline_failed,
    emit_app_resolved,
    emit_health_retrieved,
    emit_diagnosis_completed,
    emit_remediation_plan_created,
    emit_remediation_started,
    emit_action_executed,
    emit_verification_completed,
    emit_response_returned,
    emit_token_usage,
)
from db_fix.telemetry.explainability  import build_issue_explanation
from db_fix.telemetry.execution_logger import build_timeline, log_pipeline_summary
from governance.approvals import RemediationPlan, approval_store
from governance.telemetry import emit_governance_event


class DBFixAgent:

    def __init__(self) -> None:
        self.app_resolution_service = ApplicationResolutionService()
        self.health_service         = DatabaseHealthService()
        self.diagnosis_service      = DiagnosisService()
        self.remediation_service    = RemediationService()
        self.execution_service      = ExecutionService()
        self.verification_service   = VerificationService()
        self.history_service        = IncidentHistoryService()
        self.sn_notify_service      = ServiceNowNotificationService()
        self.explanation_service    = ExplanationService()

    # ─────────────────────────────────────────────────────────────────────────
    def execute(self, request: RCARequest, request_id: Optional[str] = None, require_approval: bool = True) -> dict:
        # ── Initialise trace context and metrics ──────────────────────────────
        trace   = new_trace(
            ticket_id=request.ticket_id,
            correlation_id=str(uuid.uuid4()),
            request_id=request_id,
        )
        metrics = PipelineMetrics()
        tokens  = TokenUsage()
        metrics.start_timer("total")

        ctx = bind_context(
            ticket_id=request.ticket_id,
            correlation_id=trace.correlation_id,
        )
        ctx["trace_id"]   = trace.trace_id
        ctx["request_id"] = trace.request_id

        total_start = time.perf_counter()
        timestamp   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        log_start_banner(
            ctx,
            ticket_id=request.ticket_id,
            application=request.application,
            technology=request.technology,
            correlation_id=trace.correlation_id,
            timestamp=timestamp,
        )

        tokens.record_input("request", request.model_dump())
        emit_request_received(
            trace,
            application=request.application,
            technology=request.technology,
            problem_domain=request.problem_domain,
        )

        current_step = "PIPELINE_START"

        try:
            # ══════════════════════════════════════════════════════════════════
            # STEP 01 — Application Resolution
            # ══════════════════════════════════════════════════════════════════
            current_step = "APPLICATION_RESOLUTION"
            log_step_header(1, "APPLICATION RESOLUTION",
                            "Resolve the application and app_id from the Enterprise Operations Database")
            metrics.start_timer("app_resolution")
            t0 = time.perf_counter()
            application = self.app_resolution_service.resolve(request.application, ctx=ctx)
            elapsed_app = time.perf_counter() - t0
            metrics.stop_timer("app_resolution")
            record_timing(ctx, "Application Resolution", elapsed_app)

            app_id   = application["app_id"]
            app_name = application["app_name"]

            log_info(ctx, "Application resolved",
                     step="APPLICATION_RESOLUTION",
                     app_id=app_id, app_name=app_name)

            emit_app_resolved(
                trace,
                latency_ms=elapsed_app * 1000,
                app_id=app_id,
                app_name=app_name,
            )

            # ══════════════════════════════════════════════════════════════════
            # STEP 02 — Database Health Read
            # ══════════════════════════════════════════════════════════════════
            current_step = "READ_DATABASE_HEALTH"
            log_step_header(2, "READ DATABASE HEALTH",
                            "Retrieve current health metrics from Neon PostgreSQL")
            metrics.start_timer("health_read")
            t0 = time.perf_counter()
            health = self.health_service.get_database_health(app_id, ctx=ctx)
            elapsed_health = time.perf_counter() - t0
            metrics.stop_timer("health_read")
            metrics.record_query(rows=1)
            record_timing(ctx, "Database Health Read", elapsed_health)
            initial_status = health.get("status", "UNKNOWN")

            log_health_summary(ctx, "DATABASE HEALTH SNAPSHOT", health)

            tokens.record_input("health", health)
            emit_health_retrieved(
                trace,
                latency_ms=elapsed_health * 1000,
                status=health.get("status", "UNKNOWN"),
                connections=health.get("active_connections", 0),
                cpu=health.get("cpu_usage", 0),
                memory=health.get("memory_usage", 0),
                slow_queries=health.get("slow_queries", 0),
                deadlocks=health.get("deadlocks", 0),
            )

            # ══════════════════════════════════════════════════════════════════
            # STEP 03 — Diagnosis
            # ══════════════════════════════════════════════════════════════════
            current_step = "DIAGNOSIS"
            log_step_header(3, "DIAGNOSIS",
                            "Analyse health metrics and identify issues requiring remediation")
            metrics.start_timer("diagnosis")
            t0 = time.perf_counter()
            issues = self.diagnosis_service.analyze(health)
            elapsed_diag = time.perf_counter() - t0
            metrics.stop_timer("diagnosis")
            record_timing(ctx, "Diagnosis", elapsed_diag)

            log_diagnosis_summary(ctx, issues)

            tokens.record_output("diagnosis", issues)
            emit_diagnosis_completed(
                trace,
                latency_ms=elapsed_diag * 1000,
                issue_count=len(issues),
                issues=issues,
            )

            # ══════════════════════════════════════════════════════════════════
            # STEP 04 — Remediation Plan
            # ══════════════════════════════════════════════════════════════════
            current_step = "GENERATE_REMEDIATION_PLAN"
            log_step_header(4, "GENERATE REMEDIATION PLAN",
                            "Look up remediation rules and build an action plan for each issue")
            metrics.start_timer("rule_lookup")
            t0 = time.perf_counter()
            actions = self.remediation_service.generate_plan(issues, ctx=ctx)
            elapsed_plan = time.perf_counter() - t0
            metrics.stop_timer("rule_lookup")
            metrics.record_query(rows=len(actions))
            record_timing(ctx, "Remediation Rule Lookup", elapsed_plan)

            logger.info(DIVIDER)
            logger.info(f"  REMEDIATION PLAN  ({len(actions)} action(s))")
            logger.info(DIVIDER)
            for i, a in enumerate(actions, 1):
                logger.info(f"  [{i}] Issue    : {a['issue']:<30}  "
                            f"Action: {a['action']}  Automated: {a['automated']}")
            logger.info(DIVIDER)

            tokens.record_input("plan", actions)
            emit_remediation_plan_created(
                trace,
                latency_ms=elapsed_plan * 1000,
                action_count=len(actions),
                actions=actions,
            )

            # ══════════════════════════════════════════════════════════════════
            # STEP 05 — Human Approval Gate
            # ══════════════════════════════════════════════════════════════════
            if require_approval:
                confidence = float(request.confidence or 0.0)
                plan = approval_store.create(RemediationPlan(
                    agent_type="db_fix",
                    target_type="database",
                    source_platform="servicenow",
                    issue_id=request.ticket_id,
                    issue_summary=request.reason or f"{request.problem_domain} issue for {request.application}",
                    severity="high" if request.status.upper() in {"CRITICAL", "P1", "P2"} else "medium",
                    confidence=confidence,
                    recommended_action=", ".join(a.get("action", "") for a in actions) or "No automated DB action identified",
                    expected_impact=f"Apply {len(actions)} database remediation action(s) for {app_name}.",
                    estimated_execution_time=f"{max(1, len(actions) * 2)} minutes",
                    risk_level="high" if any(a.get("automated") for a in actions) else "medium",
                    evidence=[
                        {"type": "application", "value": application},
                        {"type": "health",       "value": health},
                        {"type": "diagnosis",    "value": issues},
                    ],
                    plan=actions,
                    execution_context={"request": request.model_dump(), "request_id": request_id},
                ))
                emit_governance_event(
                    "remediation.waiting_for_approval",
                    approval_id=plan.approval_id,
                    agent_type="db_fix",
                    issue_id=request.ticket_id,
                    trace_id=trace.trace_id,
                )
                return {
                    "trace_id":        trace.trace_id,
                    "ticket_id":       request.ticket_id,
                    "correlation_id":  trace.correlation_id,
                    "request_id":      trace.request_id,
                    "agent":           "DB Fix Agent",
                    "timestamp":       timestamp,
                    "status":          "WAITING_FOR_APPROVAL",
                    "approval_id":     plan.approval_id,
                    "remediation_plan": plan.model_dump(),
                    "app_id":          app_id,
                    "app_name":        app_name,
                    "issues_found":    issues,
                    "actions":         actions,
                }

            # ══════════════════════════════════════════════════════════════════
            # STEP 06 — Execute Remediation
            # ══════════════════════════════════════════════════════════════════
            current_step = "EXECUTE_REMEDIATION"
            log_step_header(6, "EXECUTE REMEDIATION",
                            "Apply each remediation action against the target database")
            emit_remediation_started(trace, action_count=len(actions))
            metrics.start_timer("execution")
            t0 = time.perf_counter()
            execution = self.execution_service.execute(actions, app_id=app_id, ctx=ctx)
            elapsed_exec = time.perf_counter() - t0
            metrics.stop_timer("execution")
            record_timing(ctx, "Execution", elapsed_exec)

            tokens.record_output("execution", execution)
            for idx, (action, result_item) in enumerate(zip(actions, execution), 1):
                result_str = result_item.get("result", "UNKNOWN")
                if result_str == "SUCCESS":
                    metrics.record_action(success=True)
                elif result_str != "SKIPPED":
                    metrics.record_action(success=False)
                emit_action_executed(
                    trace,
                    index=idx,
                    total=len(actions),
                    action=action["action"],
                    issue=action["issue"],
                    result=result_str,
                    latency_ms=(elapsed_exec / max(len(actions), 1)) * 1000,
                )

            # ══════════════════════════════════════════════════════════════════
            # STEP 07 — Verification
            # ══════════════════════════════════════════════════════════════════
            current_step = "VERIFICATION"
            log_step_header(7, "POST-REMEDIATION VERIFICATION",
                            "Re-read database health to confirm all issues have been resolved")
            metrics.start_timer("verification")
            t0 = time.perf_counter()
            verification = self.verification_service.verify(app_id, ctx=ctx)
            elapsed_verif = time.perf_counter() - t0
            metrics.stop_timer("verification")
            metrics.record_query(rows=1)
            record_timing(ctx, "Verification", elapsed_verif)
            passed = verification.get("status") == "HEALTHY"
            metrics.verification_passed = passed

            log_verification_result(ctx, verification, passed)

            tokens.record_input("verification", verification)
            emit_verification_completed(
                trace,
                latency_ms=elapsed_verif * 1000,
                status=verification.get("status", "UNKNOWN"),
                passed=passed,
                connections=verification.get("active_connections", 0),
                cpu=verification.get("cpu_usage", 0),
                memory=verification.get("memory_usage", 0),
                deadlocks=verification.get("deadlocks", 0),
                slow_queries=verification.get("slow_queries", 0),
            )

            # ══════════════════════════════════════════════════════════════════
            # STEP 08 — Save Incident History
            # ══════════════════════════════════════════════════════════════════
            current_step = "INCIDENT_HISTORY"
            log_step_header(8, "SAVE INCIDENT HISTORY",
                            "Persist remediation actions and outcomes to the audit trail")
            metrics.start_timer("incident_history")
            t0 = time.perf_counter()
            for action in actions:
                self.history_service.save(
                    ticket_id=request.ticket_id,
                    application=app_name,
                    action=action["action"],
                    status="SUCCESS",
                    ctx=ctx,
                )
            metrics.stop_timer("incident_history")
            record_timing(ctx, "Incident History", time.perf_counter() - t0)

            # ── Finalise metrics ──────────────────────────────────────────────
            total_elapsed_s  = time.perf_counter() - total_start
            total_elapsed_ms = total_elapsed_s * 1000
            metrics.stop_timer("total")
            metrics.pipeline_success = True

            # ══════════════════════════════════════════════════════════════════
            # STEP 09 — Generate Remediation Explanation
            # ══════════════════════════════════════════════════════════════════
            current_step = "GENERATE_EXPLANATION"
            log_step_header(9, "GENERATE REMEDIATION EXPLANATION",
                            "Build a dynamic human-readable explanation from execution results")
            metrics.start_timer("explanation")
            t0_expl = time.perf_counter()
            explanation = self.explanation_service.generate(
                initial_health=health,
                issues=issues,
                actions=actions,
                execution=execution,
                verification=verification,
                final_health=verification,
                total_elapsed_ms=total_elapsed_ms,
                ctx=ctx,
            )
            action_map = {a["issue"]: a for a in actions}
            explanation["issues_explained"] = [
                build_issue_explanation(issue, action_map.get(issue["issue"]), health)
                for issue in issues
            ]
            overall_confidence = (
                round(
                    sum(e["confidence"] for e in explanation["issues_explained"]) /
                    len(explanation["issues_explained"]), 4
                ) if explanation["issues_explained"] else 1.0
            )
            explanation["overall_confidence"] = overall_confidence
            metrics.stop_timer("explanation")
            record_timing(ctx, "Explanation Generation", time.perf_counter() - t0_expl)

            # ══════════════════════════════════════════════════════════════════
            # STEP 10 — Notify ServiceNow Incident
            # ══════════════════════════════════════════════════════════════════
            current_step = "SN_NOTIFICATION"
            log_step_header(10, "NOTIFY SERVICENOW INCIDENT",
                            "Send remediation result back to the originating ServiceNow incident")
            metrics.start_timer("sn_notification")
            t0 = time.perf_counter()
            sn_notification = self.sn_notify_service.notify(
                ticket_id=request.ticket_id,
                application=app_name,
                database=app_name,
                issues=issues,
                actions=actions,
                execution=execution,
                verification=verification,
                explanation=explanation,
                total_elapsed_ms=total_elapsed_ms,
                ctx=ctx,
            )
            metrics.stop_timer("sn_notification")
            record_timing(ctx, "SN Notification", time.perf_counter() - t0)
            if sn_notification.get("notified"):
                log_info(ctx, "ServiceNow incident updated",
                         step="SN_NOTIFY",
                         sys_id=sn_notification.get("sys_id"),
                         state=sn_notification.get("state"),
                         elapsed_ms=sn_notification.get("elapsed_ms"))
            else:
                logger.warning(
                    f"[svc=DB-FIX-AGENT] [ticket={request.ticket_id}] "
                    f"[step=SN_NOTIFY] ServiceNow notification failed (non-fatal): "
                    f"{sn_notification.get('error')}"
                )

            # ── Build telemetry timeline ──────────────────────────────────────
            timeline_list = build_timeline(ctx["timeline"])

            log_execution_timeline(ctx, total_elapsed_s)
            log_final_summary(
                ctx,
                ticket_id=request.ticket_id,
                application=app_name,
                database=app_name,
                problem_domain=request.problem_domain,
                initial_status=initial_status,
                final_status=verification.get("status", "UNKNOWN"),
                issues_found=len(issues),
                actions_executed=len(actions),
                verification="PASSED" if passed else "FAILED",
                total_elapsed=total_elapsed_s,
            )

            response_payload = {
                # Traceability
                "trace_id":       trace.trace_id,
                "ticket_id":      request.ticket_id,
                "correlation_id": trace.correlation_id,
                "request_id":     trace.request_id,
                "agent":          "DB Fix Agent",
                "timestamp":      timestamp,
                # Outcome
                "status":         "SUCCESS",
                "overall_status": verification.get("status", "UNKNOWN"),
                # Application
                "app_id":         app_id,
                "app_name":       app_name,
                # Backward-compatible core fields (L2 RCA Agent reads these)
                "database":       app_name,
                "issues_found":   issues,
                "actions":        actions,
                "execution":      execution,
                "verification":   verification,
                # Telemetry
                "metrics":        metrics.to_dict(),
                "timeline":       timeline_list,
                # Explainability
                "explanation":    explanation,
                # ServiceNow notification
                "sn_notification": sn_notification,
            }
            tokens.record_output("response", response_payload)
            token_dict = tokens.to_dict()
            response_payload["token_usage"] = token_dict

            log_pipeline_summary(
                ticket_id=request.ticket_id,
                trace_id=trace.trace_id,
                correlation_id=trace.correlation_id,
                database=app_name,
                issues=issues,
                actions=actions,
                actions_succeeded=metrics.actions_succeeded,
                actions_failed=metrics.actions_failed,
                verification_status="PASSED" if passed else "FAILED",
                total_elapsed_ms=total_elapsed_ms,
                overall_status=verification.get("status", "UNKNOWN"),
                confidence=overall_confidence,
                token_usage=token_dict,
            )

            emit_token_usage(trace, token_dict)
            emit_pipeline_completed(
                trace,
                latency_ms=total_elapsed_ms,
                issues_found=len(issues),
                actions_executed=len(actions),
                initial_status=initial_status,
                final_status=verification.get("status", "UNKNOWN"),
                verification="PASSED" if passed else "FAILED",
                confidence=overall_confidence,
            )
            emit_response_returned(trace, latency_ms=total_elapsed_ms)

            return response_payload

        except Exception as e:
            total_elapsed_s  = time.perf_counter() - total_start
            total_elapsed_ms = total_elapsed_s * 1000

            log_failure_banner(ctx, e, total_elapsed_s)

            emit_pipeline_failed(
                trace,
                latency_ms=total_elapsed_ms,
                error_type=type(e).__name__,
                error_message=str(e),
                step=current_step,
            )
            raise
