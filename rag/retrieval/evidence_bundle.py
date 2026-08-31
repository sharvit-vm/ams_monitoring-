"""Build structured RCA evidence from traceback, RAG, and Neo4j expansion."""

from __future__ import annotations

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import EvidenceBundle, EvidenceCandidate, RetrievalResult
from rag.retrieval.graph_expander import expand_graph_context

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
    )


def build_evidence_bundle(
    *,
    event: ErrorEvent,
    knowledge_id: str,
    retrieval_result: RetrievalResult,
    deterministic_context: dict,
    max_connected_files: int = 5,
) -> EvidenceBundle:
    traceback_candidate = _candidate_from_traceback(event)
    rag_candidates = [_candidate_from_hit(hit) for hit in retrieval_result.hits]

    primary = traceback_candidate or (rag_candidates[0] if rag_candidates else None)
    graph_context = expand_graph_context(primary, knowledge_id, max_connected_files=max_connected_files)

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
        "rag_candidate_count": len(rag_candidates),
        "primary_candidate_source": primary.source if primary else "none",
        "source_file_read": bool(deterministic_context.get("failing_range")),
        "graph_expansion_status": graph_context.get("status"),
        "connected_file_count": len(graph_context.get("connected_files") or []),
        "same_file_neighbor_count": len(same_file_context),
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
        rag_candidates=rag_candidates,
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
