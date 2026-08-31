"""RAG indexing pipeline built on the existing repository analysis state."""

from __future__ import annotations

import os
from itertools import islice

from models import PipelineState
from rag.ingestion.chunkers import build_retrieval_chunks
from rag.indexing.lexical_index import save_lexical_index
from rag.indexing.vector_store import get_vector_store

BATCH_SIZE = int(os.getenv("RAG_VECTOR_UPSERT_BATCH", "50"))
RAG_CHUNK_MAX_CHARS = int(os.getenv("RAG_CHUNK_MAX_CHARS", "6000"))


def _batched(items: list, batch_size: int):
    iterator = iter(items)
    while batch := list(islice(iterator, batch_size)):
        yield batch


def build_rag_index(state: PipelineState, include_semantic: bool = True) -> PipelineState:
    if state.vector_complete:
        print("[RAG] Vector/lexical index already complete, skipping.")
        return state

    chunks = []
    for file_info in state.files:
        chunks.extend(build_retrieval_chunks(file_info, state.knowledge_id, max_chars=RAG_CHUNK_MAX_CHARS))

    save_lexical_index(state.knowledge_id, chunks)
    print(f"[RAG] Lexical index ready; chunks={len(chunks)}, knowledge_id={state.knowledge_id}")

    provider = os.getenv("VECTOR_STORE_PROVIDER", "pinecone").strip().lower()
    if not include_semantic or provider in {"none", "disabled", "off"}:
        reason = "disabled by workflow" if not include_semantic else "VECTOR_STORE_PROVIDER=none"
        print(f"[RAG] Semantic vector upsert skipped; reason={reason}.")
        state.vector_complete = True
        return state

    vector_store = get_vector_store()
    indexed = 0
    for batch in _batched(chunks, BATCH_SIZE):
        vector_store.upsert_chunks(batch)
        indexed += len(batch)
    print(f"[RAG] Semantic index ready; provider={provider}, vectors={indexed}, knowledge_id={state.knowledge_id}")

    state.vector_complete = True
    return state

