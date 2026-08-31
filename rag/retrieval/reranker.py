"""Deterministic reranker for incident-code retrieval evidence."""

from __future__ import annotations

from pathlib import Path

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import RetrievalHit
from rag.indexing.lexical_index import tokenize

CODE_TYPES = {"code", "code_symbol"}
SUPPORTING_TYPES = {"documentation", "configuration", "dependency"}


def _token_overlap(query_terms: set[str], value: str) -> float:
    terms = set(tokenize(value))
    if not query_terms or not terms:
        return 0.0
    return len(query_terms & terms) / max(1, len(query_terms))


def rerank_hits(event: ErrorEvent, query: str, hits: list[RetrievalHit], limit: int = 12) -> list[RetrievalHit]:
    query_terms = set(tokenize(query))
    file_hint = Path(event.file_path).name.lower() if event.file_path else ""
    function_hint = (event.function_name or "").lower()
    message_terms = set(tokenize(" ".join([event.error_type, event.message, event.short_description or ""])))

    scored: list[tuple[float, RetrievalHit]] = []
    for hit in hits:
        score = hit.score
        score += 0.25 * _token_overlap(query_terms, hit.content)
        score += 0.15 * _token_overlap(message_terms, hit.content)

        if hit.content_type in CODE_TYPES:
            score += 0.08
        elif hit.content_type in SUPPORTING_TYPES:
            score += 0.03

        if file_hint and Path(hit.file_path).name.lower() == file_hint:
            score += 0.25
        if function_hint and hit.symbol_name.lower() == function_hint:
            score += 0.25
        if event.error_type and event.error_type.lower() in hit.content.lower():
            score += 0.05
        if event.message and _token_overlap(set(tokenize(event.message)), hit.content) >= 0.25:
            score += 0.08

        hit.score = round(min(score, 2.0) / 2.0, 4)
        hit.metadata = {**hit.metadata, "reranker": "deterministic_v1"}
        scored.append((hit.score, hit))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [hit for _, hit in scored[:limit]]
