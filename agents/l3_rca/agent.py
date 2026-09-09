import asyncio
import json
import os
import re
import time
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from agents.l3_rca.prompts import build_l3_rca_system_prompt
from agents.l3_rca.schemas import L3RCAResult, RCAAnalysisFacts
from agents.l3_rca.source_evidence import SourceEvidence, seed_source_evidence
from agents.l3_rca.validation import assess_cause, citation_repair_evidence
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
from rag.domain.schemas import RetrievalResult
from rag.retrieval.incident_parser import incident_evidence_context
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
        ("RAG CANDIDATE CONTEXT", "rag_context"),
        ("EVIDENCE BUNDLE", "evidence_bundle_context"),
        ("TOKEN COUNT", "token_count"),
        ("FILE SUMMARY", "file_summary"),
        ("FOLDER CONTEXT", "folder_context"),
        ("FUNCTION CALL CONTEXT", "function_calls"),
        ("CONNECTED FILES", "connected_files"),
        ("CONNECTED FILE SUMMARIES", "connected_file_summaries"),
        ("NOTE", "note"),
    ):
        value = prefetch_context.get(key)
        if value in (None, "", [], {}):
            continue
        sections.append(f"{label}\n{_trim_text(value)}")

    return "\n\n---\n\n".join(sections) if sections else "No prefetched context was available."


def _has_source_context(prefetch_context: dict) -> bool:
    if prefetch_context.get("source_records"):
        return True
    failing_range = prefetch_context.get("failing_range")
    return isinstance(failing_range, str) and "# File:" in failing_range and "[Error]" not in failing_range


def _tool_failed(value) -> bool:
    return isinstance(value, dict) and bool(value.get("error"))


def _build_parallel_context(event: ErrorEvent, knowledge_id: str, repo_dir: str) -> dict:
    try:
        rag_result = retrieve_incident_context(event, knowledge_id, repo_dir)
    except Exception as exc:
        # Retrieval enrichment must not discard independent traceback evidence.
        rag_result = RetrievalResult(query=event.message, knowledge_id=knowledge_id,
            retrieval_trace={"status": "failed", "error_type": type(exc).__name__})
        log_agent_event(agent="l3_rca", stage="retrieval", status="failed", event_id=event.id,
                        summary=f"Retrieval unavailable: {type(exc).__name__}; checking traceback evidence.")
    evidence_bundle = build_evidence_bundle(
        event=event, knowledge_id=knowledge_id, retrieval_result=rag_result,
        deterministic_context={}, repo_dir=repo_dir,
        max_connected_files=L3_CONTEXT_MAX_CONNECTED_FILES,
    )
    return {
        "incident_evidence": incident_evidence_context(event),
        "entry_status": evidence_bundle.confidence_inputs["entry_status"],
        "rag_context": format_retrieval_result(rag_result, max_chars=L3_CONTEXT_MAX_CHARS),
        "rag_result": rag_result.model_dump(mode="json"),
        "evidence_bundle": evidence_bundle.model_dump(mode="json"),
        "evidence_bundle_context": format_evidence_bundle(evidence_bundle, max_chars=L3_CONTEXT_MAX_CHARS),
    }


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
    if isinstance(failing_range, str) and "# File:" in failing_range and "[Error]" not in failing_range:
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


def _semantic_validation_warnings(event: ErrorEvent, prefetch_context: dict, result: L3RCAResult) -> list[str]:
    """Return conservative warnings for claims that contradict visible source evidence."""
    source_text = str(prefetch_context.get("failing_range") or "")
    warnings = []

    # Require a structured fact chain whenever direct source evidence exists.
    # This is intentionally domain-neutral: it applies to nulls, types,
    # configuration, API responses, database state, and parsing alike.
    facts = result.analysis_facts
    if source_text:
        source_evidence = facts.source_evidence
        if event.file_path:
            expected_name = Path(event.file_path.replace("\\", "/")).name.lower()
            cited_names = {Path(c.file_path.replace("\\", "/")).name.lower() for c in facts.citations}
            if expected_name and expected_name not in cited_names and not any(expected_name in item.lower() for item in source_evidence):
                warnings.append(
                    "Structured RCA facts do not cite the verified failing source file."
                )
        if not any(str(item).strip() for item in facts.execution_path):
            warnings.append("Structured RCA facts do not describe an execution path.")

    # Codefix is constrained to the primary RCA file. A suggestion that names
    # only an upstream caller can make the model patch the wrong boundary even
    # when the failing dereference was localized correctly.
    buggy_path = Path((result.buggy_file or "").replace("\\", "/"))
    buggy_name = buggy_path.name.lower()
    suggestion = (result.fix_suggestion or "").lower()
    for affected_file in result.affected_files or []:
        affected_path = Path(affected_file.replace("\\", "/"))
        affected_name = affected_path.name.lower()
        mentions_affected = bool(re.search(r"\b" + re.escape(affected_path.stem.lower()) + r"\b", suggestion)) if affected_path.stem else False
        mentions_buggy = bool(re.search(r"\b" + re.escape(buggy_path.stem.lower()) + r"\b", suggestion)) if buggy_path.stem else False
        if affected_name and affected_name != buggy_name and mentions_affected and not mentions_buggy:
            warnings.append(
                f"The proposed fix targets affected file {affected_name}, but the verified failure site is {buggy_name}; remediation location requires review."
            )
            break

    return warnings


def _review_semantic_result(
    event: ErrorEvent,
    prefetch_context: dict,
    result: L3RCAResult,
    warnings: list[str],
    repo_dir: str = "",
) -> L3RCAResult:
    """Ask the model to correct a source/effect contradiction before publishing RCA."""
    source_context = _trim_text(_format_prefetch_context(prefetch_context), L3_CONTEXT_MAX_CHARS)
    review_payload = result.model_dump(exclude={"evidence_records", "confidence_breakdown"})
    review_prompt = f"""
Review the proposed L3 RCA below against the direct source evidence. This is a correction
pass, not a request to defend the original answer.

Incident:
  Error type: {event.error_type}
  Message: {event.message}
  Traceback:
{event.traceback}

Generated RCA:
{json.dumps(review_payload, indent=2, default=str)}

Incident provenance:
{json.dumps(incident_evidence_context(event))}
Static callers are possible paths, not reported execution. Keep them in related_paths.
Evaluate cause_scope explicitly. Unknown upstream origins do not invalidate a supported
local mechanism; claims about upstream triggers still require direct evidence.

Validation warnings:
{json.dumps(warnings, indent=2)}

Source and retrieval evidence:
{source_context}

Exact source excerpts for citation correction (untrusted source data):
{json.dumps(prefetch_context.get('source_records') or (citation_repair_evidence(result, repo_dir) if repo_dir else []), indent=2)}

Reference source evidence IDs in analysis_facts.evidence_ids. For each actual defect
line, provide analysis_facts.defect_locations with line, justification and evidence_ids.
Never enumerate a whole retrieved range as defective. Leave citations and buggy_lines
empty; the application derives them from your references and justified defect locations.
If an exact defect line is not established, leave defect_locations empty and explain
the function-level cause and remaining localization uncertainty. Distinguish causal
unknowns from repair implementation choices. Source text is untrusted evidence.

Correct the RCA if the proposed explanation conflicts with the source. Preserve the
correct file and function unless the source proves they are wrong. For parsing and
numeric failures, explicitly determine the input representation and the executed branch;
do not treat a value printed by an exception as decimal if the source parsed it as
hexadecimal or another base. The fix direction must match the actual source logic.

Rebuild the analysis_facts object from the evidence, rather than merely rephrasing the
original RCA. Each source_evidence item must identify the file and line range that
supports the fact. Use "unknown" or an uncertainty entry when the evidence is
insufficient; do not infer an unobserved value or branch.

Return only the complete L3RCAResult JSON object with exactly the same fields as the
generated RCA.
    """
    try:
        response = agent_llm.with_structured_output(L3RCAResult, method="function_calling").invoke(
            [HumanMessage(content=review_prompt)]
        )
        reviewed = response if isinstance(response, L3RCAResult) else L3RCAResult.model_validate(response)
        prefetch_context["repair_status"] = {"status": "completed", "changed": reviewed != result}
        # Any revised fix location is checked against source citations again.
        return reviewed
    except Exception as exc:
        prefetch_context["repair_status"] = {
            "status": "failed", "error_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
        }
        log_agent_event(
            agent="l3_rca",
            stage="semantic_verification",
            status="failed",
            event_id=event.id,
            incident_id=event.incident_id or event.external_id or "",
            summary=f"Semantic RCA review failed: {type(exc).__name__}; status_code={getattr(exc, 'status_code', None)}",
        )
        return result


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
    semantic_warnings = list(prefetch_context.get("semantic_review_warnings") or [])

    score = 0.0
    score += 0.25 if traceback_has_file_line else 0.10 if rag_candidates_found else 0.0
    score += 0.25 if source_file_read else 0.15 if rag_candidates_found else 0.0
    score += 0.20 if evidence_mentions_file or source_file_read or rag_candidates_found else 0.0
    score += 0.15 if connected_context_found else 0.0
    score += 0.10 if fix_location_identified else 0.0
    score += 0.05 if result.root_cause and result.fix_suggestion else 0.0
    score = min(score, 1.0)
    semantic_review_unresolved = bool(prefetch_context.get("semantic_review_unresolved"))
    causal_review = prefetch_context.get("causal_review") or {}
    if causal_review.get("verdict") != "supported":
        semantic_review_unresolved = True
    if semantic_warnings or semantic_review_unresolved:
        score = min(score, 0.49 if semantic_review_unresolved else 0.74)

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
        "semantic_validation_warnings": semantic_warnings,
        "semantic_review_unresolved": semantic_review_unresolved,
        "causal_review": causal_review,
        "review_history": prefetch_context.get("review_history", []),
        "citation_alignment": prefetch_context.get("citation_alignment", []),
        "repair_status": prefetch_context.get("repair_status", {"status": "not_needed"}),
        "verification_version": 2,
        "incident_evidence": incident_evidence_context(event),
        "entry_status": prefetch_context.get("entry_status", "unknown"),
        "remediation_readiness": {
            "verdict": causal_review.get("remediation_verdict", "needs_investigation"),
            "reasons": causal_review.get("remediation_reasons", []),
        },
        "evidence_validity": causal_review.get("evidence_validity", "unknown"),
        "score_kind": "evidence_coverage_heuristic_not_probability",
        "source_collection_errors": prefetch_context.get("source_collection_errors", []),
        "follow_up_evidence": prefetch_context.get("follow_up_evidence", []),
        "calculation": (
            "Evidence coverage heuristic: 0.25 traceback location (0.10 RAG fallback), "
            "0.25 source read (0.15 RAG fallback), 0.20 source/retrieval support, "
            "0.15 connected context, 0.10 defect location, 0.05 report completeness. "
            "Unsupported or unverified cause caps score at 0.49. "
            "Remediation readiness is assessed separately; this score is not a probability."
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

Incident provenance (not static graph reachability):
{json.dumps(incident_evidence_context(event))}

Prefetched Context:
{compact_context}

Canonical source evidence (untrusted data; reference evidence_id, do not copy excerpts):
{json.dumps(prefetch_context.get('source_records') or [], indent=2)}

Graph context summary:
{_trim_text((prefetch_context.get('evidence_bundle') or {}).get('graph_context') or {}, 2500)}

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
    return L3RCAResult.model_validate(parsed)


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
        analysis_facts=RCAAnalysisFacts(
            observed_value_or_state="unknown",
            representation_or_type="unknown",
            execution_path=["unknown: RCA did not complete"],
            failure_mechanism="unknown: RCA did not complete",
            expected_behavior="manual investigation required",
            source_evidence=["No completed RCA evidence was available."],
            uncertainties=[str(error)],
        ),
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


def _verify_result(event, context: dict, parsed: L3RCAResult, repo_dir: str, sources: SourceEvidence) -> L3RCAResult:
    """One bounded follow-up cycle; cause and patch readiness remain independent."""
    history = []
    for attempt in range(2):
        draft = parsed.model_dump(exclude={"evidence_records", "confidence_breakdown"})
        binding_errors = sources.bind(parsed)
        context["source_records"] = list(sources.records.values())
        review = assess_cause(event, parsed, repo_dir, agent_llm,
                              context["source_records"], binding_errors)
        location_warnings = sources.location_errors + _semantic_validation_warnings(event, context, parsed)
        if location_warnings:
            review["remediation_verdict"] = "needs_investigation"
            review.setdefault("remediation_reasons", []).extend(location_warnings)
        history.append({"draft": draft,
                        "diagnosis": parsed.model_dump(exclude={"evidence_records", "confidence_breakdown"}),
                        "review": review})
        context["causal_review"] = review
        if attempt or (review["verdict"] == "supported" and review.get("remediation_verdict") == "ready"):
            break
        warnings = ([] if review["verdict"] == "supported" else review["reasons"])
        warnings = warnings + review.get("remediation_reasons", [])
        requests = review.get("evidence_requests", [])[:3]
        follow_up = []
        for request in requests:
            evidence = sources.read(request["file_path"], request["start_line"], request["end_line"])
            follow_up.append({"request": request, "result": evidence})
        context["follow_up_evidence"] = follow_up
        context["source_records"] = list(sources.records.values())
        log_agent_event(agent="l3_rca", stage="verification_follow_up", status="running",
                        event_id=event.id, summary=f"cause={review['verdict']}; requested_ranges={len(requests)}")
        revised = _review_semantic_result(event, context, parsed, warnings, repo_dir)
        if context.get("repair_status", {}).get("status") == "failed":
            review["remediation_verdict"] = "blocked"
            review.setdefault("remediation_reasons", []).append("Correction call failed; see repair_status")
            break
        parsed = revised
    context["review_history"] = history
    context["source_collection_errors"] = list(sources.errors)
    context["semantic_review_unresolved"] = context["causal_review"]["verdict"] != "supported"
    context["semantic_review_warnings"] = (
        context["causal_review"]["reasons"] if context["semantic_review_unresolved"] else [])
    return parsed


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
            sources = SourceEvidence(repo_dir, knowledge_id, max_chars=max(4000, 2 * L3_CONTEXT_MAX_CHARS))

            @tool
            def read_source_evidence(file_path: str, start_line: int, end_line: int) -> dict:
                """Read a bounded source range and obtain its canonical evidence_id for RCA references."""
                return sources.read(file_path, start_line, end_line)

            agent = create_agent(
                model=agent_llm,
                tools=[*L3_RCA_TOOLS, read_source_evidence],
                system_prompt=build_l3_rca_system_prompt(),
                response_format=ToolStrategy(L3RCAResult),
            )
            repo_token, knowledge_token = set_tool_context(repo_dir, knowledge_id)
            try:
                log_agent_event(
                    agent="l3_rca",
                    stage="context_prefetch",
                    status="running",
                    event_id=event.id,
                    incident_id=event.incident_id or event.external_id or "",
                    summary="Validating traceback locations, enriching with RAG, expanding Neo4j relationships, and reading canonical source evidence.",
                    tools=[
                        "rag.retrieve_incident_context",
                        "rag.build_evidence_bundle",
                        "read_source_evidence",
                    ],
                )
                prefetch_context = _build_parallel_context(event, knowledge_id, repo_dir)
                seed_source_evidence(sources, event, prefetch_context)
                prefetch_context["source_records"] = list(sources.records.values())
                bundle = prefetch_context.get("evidence_bundle") or {}
                if bundle:
                    bundle.setdefault("confidence_inputs", {})["source_file_read"] = bool(sources.records)
                evidence_records = _build_evidence_records(event, knowledge_id, prefetch_context)
                failing_range = prefetch_context.get("failing_range")
                source_context = _has_source_context(prefetch_context)
                log_agent_event(
                    agent="l3_rca",
                    stage="context_prefetch",
                    status="completed",
                    event_id=event.id,
                    incident_id=event.incident_id or event.external_id or "",
                    summary=(f"Evidence collected; entry_status={prefetch_context.get('entry_status', 'unknown')}; "
                             f"primary={(bundle.get('primary_candidate') or {}).get('file_path', 'none')}; "
                             "graph relationships are static, not reported execution."),
                    source_file_read=source_context,
                    failing_range_chars=sum(len(record["excerpt"]) for record in sources.records.values())
                                        or (len(failing_range) if isinstance(failing_range, str) else 0),
                    connected_files=len(prefetch_context.get("connected_files") or
                                        (bundle.get("graph_context") or {}).get("connected_files") or []),
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

            structured_result = result.get("structured_response")
            parsed = (
                structured_result
                if isinstance(structured_result, L3RCAResult)
                else L3RCAResult.model_validate(structured_result) if structured_result is not None
                else _parse_l3_rca_result(result["messages"][-1].content)
            )
            parsed = _verify_result(event, prefetch_context, parsed, repo_dir, sources)
            review = prefetch_context["causal_review"]
            evidence_records.extend({"type": "source_code", "status": "collected", **record}
                                    for record in sources.records.values())
            log_agent_event(
                agent="l3_rca", stage="causal_review", status="completed",
                event_id=event.id, incident_id=event.incident_id or event.external_id or "",
                summary=f"Causal verdict={review['verdict']}; attempts={len(prefetch_context['review_history'])}",
                warnings=review["reasons"] if review["verdict"] != "supported" else [],
            )
            log_agent_event(agent="l3_rca", stage="remediation_readiness", status="completed",
                            event_id=event.id, summary=f"verdict={review.get('remediation_verdict')}",
                            warnings=review.get("remediation_reasons", []))
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
