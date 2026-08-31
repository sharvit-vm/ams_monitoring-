"""Expand verified retrieval hits with small same-file context windows."""

from __future__ import annotations

from rag.domain.schemas import RetrievalHit
from rag.indexing.lexical_index import load_lexical_chunks


def expand_same_file_context(knowledge_id: str, hits: list[RetrievalHit], neighbor_limit: int = 1) -> list[RetrievalHit]:
    chunks = load_lexical_chunks(knowledge_id)
    if not chunks or not hits:
        return hits

    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    for hit in hits:
        related = []
        previous_id = str(hit.metadata.get("previous_chunk_id") or "")
        next_id = str(hit.metadata.get("next_chunk_id") or "")

        for relation, chunk_id in (("previous", previous_id), ("next", next_id)):
            chunk = by_id.get(chunk_id)
            if not chunk:
                continue
            related.append({
                "relation": relation,
                "chunk_id": chunk.chunk_id,
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_name": chunk.symbol_name,
                "content_type": chunk.content_type,
                "content_preview": chunk.content[:1200],
            })
            if len(related) >= neighbor_limit * 2:
                break

        if related:
            hit.metadata = {**hit.metadata, "same_file_neighbor_chunks": related}
    return hits
