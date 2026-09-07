import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
import time

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agents.l3_rca.prompts import build_l3_rca_system_prompt
from agents.l3_rca.schemas import L3RCAResult
from config import agent_llm
from issuelayer.intake.schemas import ErrorEvent
from tools.file_tool import (
    get_token_count,
    read_file,
    read_file_range,
    reset_tool_context,
    set_tool_context,
)
from tools.neo4j_tool import (
    get_connected_files,
    get_file_summary,
    get_folder_context,
    get_function_calls,
)
from observability.agent_trace import (
    log_agent_event,
    make_evidence_record,
    trace_span,
)
from rag.retrieval.evidence_bundle import build_evidence_bundle, format_evidence_bundle
from rag.retrieval.retriever import format_retrieval_result, retrieve_incident_context


L3_RCA_TOOLS = [
    get_connected_files,
    get_function_calls,
    get_file_summary,
    get_folder_context,
    read_file,
    read_file_range,
    get_token_count,
]

L3_CONTEXT_WINDOW_LINES = int(os.getenv("L3_RCA_CONTEXT_WINDOW_LINES", "80"))
L3_CONTEXT_MAX_CONNECTED_FILES = int(os.getenv("L3_RCA_MAX_CONNECTED_FILES", "5"))
L3_CONTEXT_MAX_CHARS = int(os.getenv("L3_RCA_CONTEXT_MAX_CHARS", "12000"))


def _safe_tool_invoke(tool, payload: dict):
    try:
        return tool.invoke(payload)
    except Exception as exc:
        return {"error": str(exc)}


def _submit_tool(executor: ThreadPoolExecutor, tool, payload: dict):
    context = copy_context()
    return executor.submit(context.run, _safe_tool_invoke, tool, payload)


def _trim_text(value, max_chars: int = L3_CONTEXT_MAX_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _format_prefetch_context(prefetch_context: dict) -> str:
    """
    Render prefetched evidence as readable sections instead of one JSON blob.

    The L3 RCA model should see the failing source snippet as code evidence,
    not as an escaped JSON string buried inside metadata.
    """
    sections = []

    failing_range = prefetch_context.get("failing_range")
    if failing_range:
        sections.append(
            "SOURCE CODE EVIDENCE - FAILING FILE RANGE\n"
            "The following text is direct source code read from the cloned repository:\n"
            f"{_trim_text(failing_range)}"
        )

    for label, key in (
        ("TOKEN COUNT", "token_count"),
        ("FILE SUMMARY", "file_summary"),
        ("FOLDER CONTEXT", "folder_context"),
        ("FUNCTION CALL CONTEXT", "function_calls"),
        ("CONNECTED FILES", "connected_files"),
        ("CONNECTED FILE SUMMARIES", "connected_file_summaries"),
        ("RAG CANDIDATE CONTEXT", "rag_context"),
        ("EVIDENCE BUNDLE", "evidence_bundle_context"),
        ("NOTE", "note"),
    ):
        value = prefetch_context.get(key)
        if value in (None, "", [], {}):
            continue
        sections.append(f"{label}\n{_trim_text(value)}")

    return "\n\n---\n\n".join(sections) if sections else "No prefetched context was available."


def _has_source_context(prefetch_context: dict) -> bool:
    failing_range = prefetch_context.get("failing_range")
    return isinstance(failing_range, str) and "# File:" in failing_range and "[Error]" not in failing_range


def _tool_failed(value) -> bool:
    return isinstance(value, dict) and bool(value.get("error"))


def _build_parallel_context(event: ErrorEvent, knowledge_id: str, repo_dir: str) -> dict:
    context = {}

    if not event.file_path:
        rag_result = retrieve_incident_context(event, knowledge_id, repo_dir)
        evidence_bundle = build_evidence_bundle(
            event=event,
            knowledge_id=knowledge_id,
            retrieval_result=rag_result,
            deterministic_context=context,
            repo_dir=repo_dir,
            max_connected_files=L3_CONTEXT_MAX_CONNECTED_FILES,
        )
        return {
            "note": "No file_path available for deterministic prefetch; using RAG candidates plus Neo4j expansion.",
            "rag_context": format_retrieval_result(rag_result, max_chars=L3_CONTEXT_MAX_CHARS),
            "rag_result": rag_result.model_dump(mode="json"),
            "evidence_bundle": evidence_bundle.model_dump(mode="json"),
            "evidence_bundle_context": format_evidence_bundle(evidence_bundle, max_chars=L3_CONTEXT_MAX_CHARS),
        }

    start_line = max(1, (event.line_number or 1) - L3_CONTEXT_WINDOW_LINES // 2)
    end_line = max(start_line, (event.line_number or start_line) + L3_CONTEXT_WINDOW_LINES // 2)

    tasks = {
        "failing_range": (read_file_range, {"file_path": event.file_path, "start_line": start_line, "end_line": end_line}),
        "token_count": (get_token_count, {"file_path": event.file_path}),
        "file_summary": (get_file_summary, {"file_path": event.file_path, "knowledge_id": knowledge_id}),
        "folder_context": (get_folder_context, {"file_path": event.file_path, "knowledge_id": knowledge_id}),
        "connected_files": (get_connected_files, {"file_path": event.file_path, "knowledge_id": knowledge_id}),
    }
    if event.function_name:
        tasks["function_calls"] = (
            get_function_calls,
            {"function_name": event.function_name, "file_path": event.file_path, "knowledge_id": knowledge_id},
        )

    with ThreadPoolExecutor(max_workers=min(7, len(tasks) + 1)) as executor:
        futures = {
            name: _submit_tool(executor, tool, payload)
            for name, (tool, payload) in tasks.items()
        }
        rag_future = executor.submit(retrieve_incident_context, event, knowledge_id, repo_dir)
        for name, future in futures.items():
            context[name] = future.result()
        rag_result = rag_future.result()

    connected_files = context.get("connected_files") or []
    if isinstance(connected_files, list):
        connected_files = connected_files[:L3_CONTEXT_MAX_CONNECTED_FILES]
        context["connected_files"] = connected_files

        summary_tasks = {
            file_path: (get_file_summary, {"file_path": file_path, "knowledge_id": knowledge_id})
            for file_path in connected_files
        }
        with ThreadPoolExecutor(max_workers=max(1, min(4, len(summary_tasks)))) as executor:
            futures = {
                file_path: _submit_tool(executor, tool, payload)
                for file_path, (tool, payload) in summary_tasks.items()
            }
            context["connected_file_summaries"] = {
                file_path: future.result()
                for file_path, future in futures.items()
            }

    evidence_bundle = build_evidence_bundle(
        event=event,
        knowledge_id=knowledge_id,
        retrieval_result=rag_result,
        deterministic_context=context,
        repo_dir=repo_dir,
        max_connected_files=L3_CONTEXT_MAX_CONNECTED_FILES,
    )
    context["rag_context"] = format_retrieval_result(rag_result, max_chars=L3_CONTEXT_MAX_CHARS)
    context["rag_result"] = rag_result.model_dump(mode="json")
    context["evidence_bundle"] = evidence_bundle.model_dump(mode="json")
    context["evidence_bundle_context"] = format_evidence_bundle(evidence_bundle, max_chars=L3_CONTEXT_MAX_CHARS)
    return context


def _build_evidence_records(event: ErrorEvent, knowledge_id: str, prefetch_context: dict) -> list[dict]:
    records = []
    start_line = max(1, (event.line_number or 1) - L3_CONTEXT_WINDOW_LINES // 2)
    end_line = max(start_line, (event.line_number or start_line) + L3_CONTEXT_WINDOW_LINES // 2)

    rag_result = prefetch_context.get("rag_result") or {}
    rag_hits = rag_result.get("hits") or [] if isinstance(rag_result, dict) else []
    if rag_hits:
        top_hit = rag_hits[0]
        records.append(make_evidence_record(
            evidence_id="l3_ev_rag_001",
            evidence_type="retrieval",
            tool="rag.retrieve_incident_context",
            file_path=top_hit.get("file_path"),
            line_start=top_hit.get("start_line"),
            line_end=top_hit.get("end_line"),
            summary=(
                f"RAG selected {len(rag_hits)} verified candidate chunk(s); "
                f"top_file={top_hit.get('file_path')} score={top_hit.get('score')}"
            ),
            confidence_impact="medium",
            metadata={"knowledge_id": knowledge_id, "hits": rag_hits[:5]},
        ))

    evidence_bundle = prefetch_context.get("evidence_bundle") or {}
    graph_context = evidence_bundle.get("graph_context") or {} if isinstance(evidence_bundle, dict) else {}
    if evidence_bundle:
        records.append(make_evidence_record(
            evidence_id="l3_ev_bundle_001",
            evidence_type="evidence_bundle",
            tool="rag.build_evidence_bundle",
            file_path=(evidence_bundle.get("primary_candidate") or {}).get("file_path") if isinstance(evidence_bundle, dict) else None,
            summary=(evidence_bundle.get("evidence_summary") or "Built structured evidence bundle from traceback, RAG, and Neo4j.") if isinstance(evidence_bundle, dict) else "Built structured evidence bundle.",
            confidence_impact="high" if graph_context.get("status") == "completed" else "medium",
            metadata=evidence_bundle if isinstance(evidence_bundle, dict) else {},
        ))

    failing_range = prefetch_context.get("failing_range")
    if _has_source_context(prefetch_context):
        records.append(make_evidence_record(
            evidence_id="l3_ev_001",
            evidence_type="source_code",
            tool="read_file_range",
            file_path=event.file_path,
            line_start=start_line,
            line_end=end_line,
            summary="Read the failing source-code range from the cloned repository.",
            confidence_impact="high",
            metadata={"knowledge_id": knowledge_id},
        ))
    elif failing_range:
        records.append(make_evidence_record(
            evidence_id="l3_ev_001",
            evidence_type="source_code",
            tool="read_file_range",
            status="failed",
            file_path=event.file_path,
            line_start=start_line,
            line_end=end_line,
            summary=_trim_text(failing_range, 180),
            confidence_impact="low",
            metadata={"knowledge_id": knowledge_id},
        ))

    token_count = prefetch_context.get("token_count")
    if token_count and not _tool_failed(token_count):
        records.append(make_evidence_record(
            evidence_id="l3_ev_002",
            evidence_type="file_metadata",
            tool="get_token_count",
            file_path=event.file_path,
            summary=(
                f"Measured file size: {token_count.get('line_count', 'unknown')} lines, "
                f"{token_count.get('token_count', 'unknown')} tokens."
            ),
            confidence_impact="neutral",
            metadata=token_count,
        ))

    file_summary = prefetch_context.get("file_summary")
    if file_summary and not _tool_failed(file_summary):
        records.append(make_evidence_record(
            evidence_id="l3_ev_003",
            evidence_type="code_graph",
            tool="get_file_summary",
            file_path=event.file_path,
            summary="Fetched Neo4j file summary and function/class counts for the failing file.",
            confidence_impact="medium",
            metadata=file_summary,
        ))

    function_calls = prefetch_context.get("function_calls")
    if function_calls and not _tool_failed(function_calls):
        call_count = len(function_calls.get("calls") or [])
        called_by_count = len(function_calls.get("called_by") or [])
        records.append(make_evidence_record(
            evidence_id="l3_ev_004",
            evidence_type="code_graph",
            tool="get_function_calls",
            file_path=event.file_path,
            summary=f"Checked call graph: calls={call_count}, called_by={called_by_count}.",
            confidence_impact="medium" if call_count or called_by_count else "neutral",
            metadata=function_calls,
        ))

    connected_files = prefetch_context.get("connected_files")
    if isinstance(connected_files, list):
        records.append(make_evidence_record(
            evidence_id="l3_ev_005",
            evidence_type="code_graph",
            tool="get_connected_files",
            file_path=event.file_path,
            summary=f"Found {len(connected_files)} connected file(s) from imports/calls graph.",
            confidence_impact="medium" if connected_files else "neutral",
            metadata={"connected_files": connected_files},
        ))

    folder_context = prefetch_context.get("folder_context")
    if folder_context and not _tool_failed(folder_context):
        records.append(make_evidence_record(
            evidence_id="l3_ev_006",
            evidence_type="code_graph",
            tool="get_folder_context",
            file_path=event.file_path,
            summary="Fetched folder/module context for the failing file.",
            confidence_impact="neutral",
            metadata=folder_context,
        ))

    return records


def _confidence_level(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _calculate_confidence(event: ErrorEvent, prefetch_context: dict, result: L3RCAResult) -> tuple[float, str, dict]:
    source_file_read = _has_source_context(prefetch_context)
    function_calls = prefetch_context.get("function_calls") or {}
    connected_files = prefetch_context.get("connected_files") or []
    rag_hits = (prefetch_context.get("rag_result") or {}).get("hits") or []
    rag_candidates_found = bool(rag_hits)
    traceback_has_file_line = bool(event.file_path and event.line_number and event.traceback)
    evidence_bundle = prefetch_context.get("evidence_bundle") or {}
    bundle_graph = evidence_bundle.get("graph_context") or {} if isinstance(evidence_bundle, dict) else {}
    bundle_confidence = evidence_bundle.get("confidence_inputs") or {} if isinstance(evidence_bundle, dict) else {}
    graph_expansion_found = bundle_graph.get("status") == "completed" and (
        bool(bundle_graph.get("connected_files"))
        or bool((bundle_graph.get("function_calls") or {}).get("calls"))
        or bool((bundle_graph.get("function_calls") or {}).get("called_by"))
        or bool(bundle_graph.get("file_summary"))
    )
    connected_context_found = (
        bool(connected_files)
        or bool(function_calls.get("calls"))
        or bool(function_calls.get("called_by"))
        or graph_expansion_found
    )
    fix_location_identified = bool(result.buggy_file and result.buggy_lines)
    evidence_mentions_file = any(
        event.file_path and event.file_path in str(item)
        for item in (result.evidence or [])
    )

    score = 0.0
    score += 0.25 if traceback_has_file_line else 0.10 if rag_candidates_found else 0.0
    score += 0.25 if source_file_read else 0.15 if rag_candidates_found else 0.0
    score += 0.20 if evidence_mentions_file or source_file_read or rag_candidates_found else 0.0
    score += 0.15 if connected_context_found else 0.0
    score += 0.10 if fix_location_identified else 0.0
    score += 0.05 if result.root_cause and result.fix_suggestion else 0.0
    score = min(score, 1.0)

    breakdown = {
        "traceback_has_file_line": traceback_has_file_line,
        "source_file_read": source_file_read,
        "rag_candidates_found": rag_candidates_found,
        "failing_line_or_file_supported_by_evidence": evidence_mentions_file or source_file_read or rag_candidates_found,
        "connected_context_found": connected_context_found,
        "graph_expansion_found": graph_expansion_found,
        "evidence_bundle_inputs": bundle_confidence,
        "fix_location_identified": fix_location_identified,
        "root_cause_and_fix_suggestion_present": bool(result.root_cause and result.fix_suggestion),
        "calculation": (
            "0.25 traceback file/line + 0.25 source read + 0.20 source/evidence match "
            "+ 0.15 connected context + 0.10 fix location + 0.05 RCA completeness"
        ),
    }
    return score, _confidence_level(score), breakdown


def _build_user_message(event: ErrorEvent, knowledge_id: str, prefetch_context: dict) -> str:
    compact_context = _trim_text(_format_prefetch_context(prefetch_context))
    source_context_status = (
        "available"
        if _has_source_context(prefetch_context)
        else "not_available_or_file_read_failed"
    )
    return f"""
Error Event:
  File        : {event.file_path}
  Line        : {event.line_number}
  Function    : {event.function_name}
  Error type  : {event.error_type}
  Message     : {event.message}
  Knowledge ID: {knowledge_id}
  Source Context Status: {source_context_status}

Traceback:
{event.traceback}

Prefetched Context:
{compact_context}

Investigate this bug and return your L3RCAResult JSON.
"""


def _parse_l3_rca_result(content: str) -> L3RCAResult:
    clean = content.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
        clean = clean.strip()

    parsed = json.loads(clean)
    return L3RCAResult(**parsed)


def _fallback_result(event: ErrorEvent, error: Exception) -> L3RCAResult:
    return L3RCAResult(
        root_cause=f"L3 RCA agent failed: {str(error)}",
        buggy_file=event.file_path,
        buggy_function=event.function_name,
        buggy_lines=[event.line_number] if event.line_number else [],
        affected_files=[],
        fix_suggestion="Manual investigation required",
        confidence="low",
        confidence_score=0.0,
        confidence_breakdown={"failure": str(error)},
        reasoning=f"Agent encountered an error: {str(error)}",
        evidence=[f"L3 RCA failed before completing analysis: {str(error)}"],
        evidence_records=[
            make_evidence_record(
                evidence_id="l3_ev_error",
                evidence_type="agent_failure",
                tool="l3_rca_agent",
                status="failed",
                summary=f"L3 RCA failed before completing analysis: {str(error)}",
                confidence_impact="low",
            )
        ],
    )


def run_l3_rca(event: ErrorEvent, knowledge_id: str, repo_dir: str = "clone") -> L3RCAResult:
    """
    Run the L3 RCA agent for a code-level incident.

    L3 means deep code RCA: read source files, inspect dependency context, and
    return a structured diagnosis that downstream remediation agents can use.
    """
    started_at = time.perf_counter()
    log_agent_event(
        agent="l3_rca",
        stage="started",
        status="running",
        event_id=event.id,
        incident_id=event.incident_id or event.external_id or "",
        repo=event.repo_full_name,
        file=event.file_path,
        line=event.line_number,
        function=event.function_name,
    )

    try:
        with trace_span(
            name="l3_rca",
            event_id=event.id,
            incident_id=event.incident_id or event.external_id or "",
            agent="l3_rca",
            input_data={
                "file_path": event.file_path,
                "line_number": event.line_number,
                "function_name": event.function_name,
                "error_type": event.error_type,
                "message": event.message,
                "knowledge_id": knowledge_id,
            },
            metadata={"repo": event.repo_full_name, "knowledge_id": knowledge_id},
        ):
            agent = create_agent(
                model=agent_llm,
                tools=L3_RCA_TOOLS,
                system_prompt=build_l3_rca_system_prompt(),
            )
            repo_token, knowledge_token = set_tool_context(repo_dir, knowledge_id)
            try:
                log_agent_event(
                    agent="l3_rca",
                    stage="context_prefetch",
                    status="running",
                    event_id=event.id,
                    incident_id=event.incident_id or event.external_id or "",
                    summary="Collecting deterministic source context, hybrid RAG candidates, and Neo4j expansion evidence.",
                    tools=[
                        "read_file_range",
                        "get_token_count",
                        "get_file_summary",
                        "get_folder_context",
                        "get_connected_files",
                        "get_function_calls" if event.function_name else "",
                        "rag.retrieve_incident_context",
                        "rag.build_evidence_bundle",
                    ],
                )
                prefetch_context = _build_parallel_context(event, knowledge_id, repo_dir)
                evidence_records = _build_evidence_records(event, knowledge_id, prefetch_context)
                failing_range = prefetch_context.get("failing_range")
                source_context = _has_source_context(prefetch_context)
                log_agent_event(
                    agent="l3_rca",
                    stage="context_prefetch",
                    status="completed",
                    event_id=event.id,
                    incident_id=event.incident_id or event.external_id or "",
                    summary="Evidence collected before LLM RCA.",
                    source_file_read=source_context,
                    failing_range_chars=len(failing_range) if isinstance(failing_range, str) else 0,
                    connected_files=len(prefetch_context.get("connected_files") or []),
                    rag_hits=len((prefetch_context.get("rag_result") or {}).get("hits") or []),
                    graph_expansion_status=((prefetch_context.get("evidence_bundle") or {}).get("graph_context") or {}).get("status"),
                    evidence_ids=[record["evidence_id"] for record in evidence_records],
                )
                log_agent_event(
                    agent="l3_rca",
                    stage="llm_analysis",
                    status="running",
                    event_id=event.id,
                    incident_id=event.incident_id or event.external_id or "",
                    summary="Sending bounded evidence context to L3 RCA agent.",
                )
                result = agent.invoke({
                    "messages": [HumanMessage(content=_build_user_message(event, knowledge_id, prefetch_context))]
                })
            finally:
                reset_tool_context(repo_token, knowledge_token)

            parsed = _parse_l3_rca_result(result["messages"][-1].content)
            score, level, breakdown = _calculate_confidence(event, prefetch_context, parsed)
            parsed.confidence_score = score
            parsed.confidence = level
            parsed.confidence_breakdown = breakdown
            parsed.evidence_records = evidence_records
            log_agent_event(
                agent="l3_rca",
                stage="confidence_calculation",
                status="completed",
                event_id=event.id,
                incident_id=event.incident_id or event.external_id or "",
                confidence=level,
                score=round(score, 2),
                evidence_ids=[record["evidence_id"] for record in evidence_records],
                calculation=breakdown["calculation"],
            )
            log_agent_event(
                agent="l3_rca",
                stage="completed",
                status="completed",
                event_id=event.id,
                incident_id=event.incident_id or event.external_id or "",
                confidence=parsed.confidence,
                score=round(parsed.confidence_score, 2),
                buggy_file=parsed.buggy_file,
                root_cause=parsed.root_cause,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            return parsed

    except Exception as exc:
        log_agent_event(
            agent="l3_rca",
            stage="failed",
            status="failed",
            event_id=event.id,
            incident_id=event.incident_id or event.external_id or "",
            summary=str(exc),
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return _fallback_result(event, exc)


async def async_run_l3_rca(event: ErrorEvent, knowledge_id: str, repo_dir: str = "clone") -> L3RCAResult:
    return await asyncio.to_thread(run_l3_rca, event, knowledge_id, repo_dir)
