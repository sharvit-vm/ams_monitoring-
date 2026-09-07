"""Build structured RCA evidence from traceback, RAG, and Neo4j expansion."""

from __future__ import annotations

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import EvidenceBundle, EvidenceCandidate, RetrievalResult
from rag.retrieval.graph_expander import expand_graph_candidates, expand_graph_context
from rag.retrieval.incident_parser import resolve_traceback_frames

DOC_TYPES = {"documentation"}
CONFIG_TYPES = {"configuration", "dependency"}


def _candidate_from_traceback(event: ErrorEvent) -> EvidenceCandidate | None:
    if not event.file_path:
        return None
    return EvidenceCandidate(
        source="traceback",
        file_path=event.file_path,
        symbol_name=event.function_name or "",
        start_line=event.line_number or 0,
        end_line=event.line_number or 0,
        score=1.0 if event.line_number else 0.75,
        reason="Normalized incident provided traceback file path and line/function hints.",
    )


def _candidate_from_hit(hit, source: str = "rag") -> EvidenceCandidate:
    return EvidenceCandidate(
        source=source,
        file_path=hit.file_path,
        symbol_name=hit.symbol_name,
        start_line=hit.start_line,
        end_line=hit.end_line,
        score=hit.score,
        reason=f"Selected by {hit.source or 'hybrid'} retrieval; content_type={hit.content_type}.",
        role="retrieval_candidate",
    )


def build_evidence_bundle(
    *,
    event: ErrorEvent,
    knowledge_id: str,
    retrieval_result: RetrievalResult,
    deterministic_context: dict,
    repo_dir: str = "",
    max_connected_files: int = 5,
) -> EvidenceBundle:
    traceback_candidate = _candidate_from_traceback(event)
    traceback_frames = resolve_traceback_frames(event.traceback, repo_dir)
    frame_candidates = [EvidenceCandidate(
        source="traceback_frame",
        role="failure_site" if index == 0 else "traceback_candidate",
        frame_index=frame.frame_index,
        file_path=frame.file_path,
        symbol_name=frame.function_name,
        start_line=frame.line_number,
        end_line=frame.line_number,
        score=max(0.1, 1.0 - index * 0.04),
        reason="Resolved traceback frame against the checked-out repository.",
    ) for index, frame in enumerate(traceback_frames) if frame.file_path]
    rag_candidates = [_candidate_from_hit(hit) for hit in retrieval_result.hits]

    framework_indexes = {frame.frame_index for frame in traceback_frames if frame.framework_frame}
    primary = next(
        (candidate for candidate in frame_candidates if candidate.frame_index not in framework_indexes),
        None,
    )
    primary = primary or traceback_candidate or (rag_candidates[0] if rag_candidates else None)
    graph_context = expand_graph_context(primary, knowledge_id, max_connected_files=max_connected_files)
    graph_candidates = expand_graph_candidates(
        [primary, *frame_candidates, *rag_candidates], knowledge_id,
        max_candidates=3, max_connected_files=max_connected_files,
    )

    same_file_context = []
    doc_context = []
    config_context = []
    for hit in retrieval_result.hits:
        same_file_context.extend(hit.metadata.get("same_file_neighbor_chunks") or [])
        if hit.content_type in DOC_TYPES:
            doc_context.append(hit.model_dump(mode="json"))
        elif hit.content_type in CONFIG_TYPES:
            config_context.append(hit.model_dump(mode="json"))

    confidence_inputs = {
        "traceback_candidate_present": traceback_candidate is not None,
        "traceback_frame_count": len(traceback_frames),
        "resolved_traceback_frame_count": len(frame_candidates),
        "rag_candidate_count": len(rag_candidates),
        "primary_candidate_source": primary.source if primary else "none",
        "source_file_read": bool(deterministic_context.get("failing_range")),
        "graph_expansion_status": graph_context.get("status"),
        "connected_file_count": len(graph_context.get("connected_files") or []),
        "same_file_neighbor_count": len(same_file_context),
        "candidate_graph_context_count": len(graph_candidates),
    }
    summary = (
        f"Evidence bundle built; primary={primary.source if primary else 'none'}, "
        f"rag_candidates={len(rag_candidates)}, "
        f"graph_status={graph_context.get('status')}, "
        f"same_file_neighbors={len(same_file_context)}"
    )
    return EvidenceBundle(
        knowledge_id=knowledge_id,
        primary_candidate=primary,
        traceback_candidate=traceback_candidate,
        traceback_frames=traceback_frames,
        traceback_candidates=frame_candidates,
        rag_candidates=rag_candidates,
        candidate_graph_contexts=graph_candidates,
        same_file_context=same_file_context[:6],
        graph_context=graph_context,
        doc_context=doc_context[:5],
        config_context=config_context[:5],
        retrieval_trace=retrieval_result.retrieval_trace,
        confidence_inputs=confidence_inputs,
        evidence_summary=summary,
    )


def format_evidence_bundle(bundle: EvidenceBundle, max_chars: int = 12000) -> str:
    payload = bundle.model_dump(mode="json")
    text = str(payload)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[evidence bundle truncated]"
