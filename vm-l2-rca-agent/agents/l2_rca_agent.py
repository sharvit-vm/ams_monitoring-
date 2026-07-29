import time
import uuid
from datetime import datetime, timezone

from services.agent_executor import AgentExecutor
from models.request import IncidentRequest
from models.api_response import APIResponse, ExecutionBlock, MetricsBlock
from utils.logger import log
from services.context_builder import ContextBuilder
from services.diagnosis_service import DiagnosisService
from services.report_service import ReportService
from telemetry.tracer import new_trace_id, get_trace_id
from telemetry.logger import tlog
from telemetry.explainability import build_explanation
from telemetry.metrics import RequestMetrics
from telemetry.execution_logger import ExecutionLogger

_xlog = ExecutionLogger("L2 RCA Agent")


class L2RCAAgent:

    def __init__(self):
        self.context_builder = ContextBuilder()
        self.diagnosis_service = DiagnosisService()
        self.report_service = ReportService()
        self.agent_executor = AgentExecutor()

    def analyze(self, incident: IncidentRequest) -> APIResponse:

        trace_id = get_trace_id() or new_trace_id()
        correlation_id = trace_id
        ids = {"ticket_id": incident.ticket_id, "correlation_id": correlation_id}

        pipeline_start = time.perf_counter()
        start_time = datetime.now(timezone.utc).isoformat()

        metrics = RequestMetrics(
            trace_id=trace_id,
            ticket_id=incident.ticket_id,
            start_time=pipeline_start,
            input_size=len(str(incident.model_dump()))
        )

        # ── Human-readable pipeline banner ────────────────────────────────────
        _xlog.banner(f"Analyzing Incident  {incident.ticket_id}")

        # ── Stage 1: Incident received ────────────────────────────────────────
        _xlog.step(1, "INCIDENT RECEIVED", "Validate and register the incoming incident payload")
        _xlog.substep("CONNECTING", f"Ticket       : {incident.ticket_id}")
        _xlog.substep("CONNECTING", f"Application  : {incident.application}")
        _xlog.substep("CONNECTING", f"Technology   : {incident.technology}")
        _xlog.substep("CONNECTING", f"Priority     : {incident.priority}")
        _xlog.substep("CONNECTING", f"Trace ID     : {trace_id}")
        _xlog.substep("COMPLETED", "Incident payload validated and registered")

        tlog("INCIDENT_RECEIVED", event="INCIDENT_RECEIVED", status="STARTED",
             extra={
                 "ticket_id": incident.ticket_id,
                 "application": incident.application,
                 "technology": incident.technology,
                 "priority": incident.priority
             })

        # ── Stage 2: Context building ─────────────────────────────────────────
        _xlog.step(2, "CONTEXT BUILDING", "Extract and structure incident fields for LLM input")
        _xlog.substep("EXECUTING", "Extracting fields from incident payload")
        log("Context building", stage="CONTEXT_BUILDING", **ids)
        t_ctx = time.perf_counter()
        context = self.context_builder.build_context(incident, correlation_id=correlation_id)
        ctx_elapsed = (time.perf_counter() - t_ctx) * 1000
        _xlog.substep("COMPLETED", f"Context built with {len(context)} fields", ctx_elapsed)

        # ── Stage 3: LLM diagnosis ────────────────────────────────────────────
        _xlog.step(3, "LLM DIAGNOSIS", "Send context to Groq LLM and parse root cause analysis")
        _xlog.substep("CONNECTING", "Establishing connection to Groq API")
        _xlog.substep("EXECUTING", "Sending prompt to llama-3.3-70b-versatile")

        tlog("LLM_CALL", event="LLM_CALL", status="STARTED",
             extra={"ticket_id": incident.ticket_id})

        diagnosis, llm_latency_ms, token_usage = self.diagnosis_service.diagnose(
            context,
            ticket_id=incident.ticket_id,
            correlation_id=correlation_id
        )

        metrics.llm_latency_ms = llm_latency_ms
        metrics.token_usage = token_usage
        metrics.confidence = diagnosis.confidence
        metrics.decision = diagnosis.recommended_agent

        _xlog.substep("COMPLETED", f"LLM responded — tokens used: {token_usage}", llm_latency_ms)
        _xlog.substep("COMPLETED", f"Problem domain   : {diagnosis.problem_domain}")
        _xlog.substep("COMPLETED", f"Recommended agent: {diagnosis.recommended_agent}")
        _xlog.substep("COMPLETED", f"Confidence       : {diagnosis.confidence}")

        tlog("LLM_COMPLETED", event="LLM_COMPLETED", status="SUCCESS",
             decision=diagnosis.recommended_agent,
             confidence=diagnosis.confidence,
             latency_ms=llm_latency_ms,
             extra={"token_usage": token_usage, "problem_domain": diagnosis.problem_domain})

        # ── Stage 4: Generate RCA report ──────────────────────────────────────
        _xlog.step(4, "RCA REPORT", "Generate structured Root Cause Analysis report")
        _xlog.substep("EXECUTING", "Building RCA report from diagnosis")
        report = self.report_service.generate_report(
            ticket_id=incident.ticket_id,
            application=incident.application,
            technology=incident.technology,
            diagnosis=diagnosis
        )
        _xlog.substep("COMPLETED", f"Report status    : {report.status}")
        _xlog.substep("COMPLETED", f"Target resource  : {report.target_resource.resource_type}")

        log("RCA report generated", stage="RCA_REPORT_GENERATED",
            extra={"status": report.status}, **ids)

        # ── Stage 5: Build explanation ────────────────────────────────────────
        _xlog.step(5, "EXPLAINABILITY", "Build structured explanation for the RCA decision")
        _xlog.substep("EXECUTING", "Generating explanation from diagnosis data")
        explanation = build_explanation(
            decision=diagnosis.recommended_agent,
            reason=diagnosis.reason,
            confidence=diagnosis.confidence,
            recommended_checks=diagnosis.recommended_checks,
            execution_summary=(
                f"The LLM analyzed the incident and identified a "
                f"{diagnosis.problem_domain}-related failure with "
                f"{round(diagnosis.confidence * 100)}% confidence."
            ),
            alternative_agents=[
                {"agent": a.agent, "confidence": a.confidence}
                for a in diagnosis.alternative_agents
            ]
        )
        _xlog.substep("COMPLETED", f"Decision         : {explanation.decision}")
        _xlog.substep("COMPLETED", f"Reason           : {explanation.reason[:60]}...")
        for i, ev in enumerate(explanation.evidence, 1):
            _xlog.substep("EVIDENCE", f"[{i}] {ev}")

        tlog("EXPLANATION_GENERATED", event="EXPLANATION_GENERATED", status="SUCCESS",
             decision=diagnosis.recommended_agent, confidence=diagnosis.confidence)

        # ── Stage 6: Execute downstream agent ────────────────────────────────
        _xlog.step(6, "DOWNSTREAM EXECUTION", f"Delegate to {report.recommended_agent} for remediation")
        _xlog.substep("CONNECTING", f"Target agent     : {report.recommended_agent}")
        _xlog.substep("EXECUTING", "Sending HTTP POST to downstream agent")

        log("Preparing request for downstream agent",
            stage="PREPARING_DOWNSTREAM_REQUEST",
            extra={"target_agent": report.recommended_agent}, **ids)

        t0 = time.perf_counter()
        execution = self.agent_executor.execute(report)
        execution_elapsed = (time.perf_counter() - t0) * 1000

        _xlog.substep("COMPLETED", "Response received from downstream agent", execution_elapsed)

        log("Response received from downstream agent",
            stage="RESPONSE_RECEIVED", elapsed_ms=execution_elapsed, **ids)

        # ── Stage 7: Build final response ─────────────────────────────────────
        total_elapsed = (time.perf_counter() - pipeline_start) * 1000
        end_time = datetime.now(timezone.utc).isoformat()

        metrics.end_time = time.perf_counter()
        metrics.duration_ms = total_elapsed
        metrics.output_size = len(str(execution))
        metrics.success = True

        tlog("RESPONSE_RETURNED", event="RESPONSE_RETURNED", status="SUCCESS",
             decision=diagnosis.recommended_agent, confidence=diagnosis.confidence,
             latency_ms=total_elapsed,
             extra={
                 "ticket_id": incident.ticket_id,
                 "problem_domain": diagnosis.problem_domain,
                 "recommended_agent": report.recommended_agent,
                 "pipeline_duration_ms": round(total_elapsed, 3)
             })

        # ── Human-readable pipeline summary ───────────────────────────────────
        _xlog.summary({
            "Trace ID"         : trace_id,
            "Ticket ID"        : incident.ticket_id,
            "Application"      : incident.application,
            "Technology"       : incident.technology,
            "Problem Domain"   : diagnosis.problem_domain,
            "Decision"         : diagnosis.recommended_agent,
            "Confidence"       : f"{diagnosis.confidence} ({round(diagnosis.confidence * 100)}%)",
            "LLM Latency"      : f"{round(llm_latency_ms, 1)} ms",
            "Token Usage"      : token_usage,
            "Execution Time"   : f"{round(execution_elapsed, 1)} ms",
            "Total Duration"   : f"{round(total_elapsed, 1)} ms",
            "Status"           : "SUCCESS ✓"
        })

        return APIResponse(
            trace_id=trace_id,
            agent="L2 RCA",
            status="SUCCESS",
            decision=diagnosis.recommended_agent,
            confidence=diagnosis.confidence,
            explanation=explanation,
            execution=ExecutionBlock(
                start_time=start_time,
                end_time=end_time,
                duration_ms=round(total_elapsed, 3)
            ),
            metrics=MetricsBlock(
                llm_latency_ms=round(llm_latency_ms, 3),
                token_usage=token_usage,
                input_size=metrics.input_size,
                output_size=metrics.output_size,
                confidence=diagnosis.confidence,
                success=True
            ),
            message="Diagnosis completed successfully.",
            data={"rca": report, "execution": execution}
        )
