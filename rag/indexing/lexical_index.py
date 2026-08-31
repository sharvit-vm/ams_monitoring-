"""Local BM25-style lexical index for hybrid retrieval."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from config import CACHE_DIR
from rag.domain.schemas import RetrievalChunk, RetrievalHit

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def _index_path(knowledge_id: str) -> Path:
    path = Path(CACHE_DIR) / knowledge_id / "rag"
    path.mkdir(parents=True, exist_ok=True)
    return path / "lexical_index.json"


def save_lexical_index(knowledge_id: str, chunks: list[RetrievalChunk]) -> None:
    payload = [chunk.model_dump(mode="json") for chunk in chunks]
    with open(_index_path(knowledge_id), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def load_lexical_chunks(knowledge_id: str) -> list[RetrievalChunk]:
    path = _index_path(knowledge_id)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return [RetrievalChunk(**item) for item in raw]


def _hit_from_chunk(chunk: RetrievalChunk, score: float) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.chunk_id,
        file_path=chunk.file_path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        score=score,
        source="bm25",
        content_type=chunk.content_type,
        language=chunk.language,
        symbol_name=chunk.symbol_name,
        symbol_type=chunk.symbol_type,
        content=chunk.content,
        metadata={
            **chunk.metadata,
            "parent_file_id": chunk.parent_file_id,
            "parent_symbol_id": chunk.parent_symbol_id,
            "chunk_order": chunk.chunk_order,
            "previous_chunk_id": chunk.previous_chunk_id,
            "next_chunk_id": chunk.next_chunk_id,
            "imports": chunk.imports,
            "called_symbols": chunk.called_symbols,
            "exported_symbols": chunk.exported_symbols,
            "content_hash": chunk.content_hash,
        },
    )


def search_lexical(knowledge_id: str, query: str, top_k: int = 50) -> list[RetrievalHit]:
    chunks = load_lexical_chunks(knowledge_id)
    if not chunks:
        return []

    query_terms = tokenize(query)
    if not query_terms:
        return []

    chunk_term_counts: list[Counter[str]] = []
    chunk_lengths: list[int] = []
    doc_freq: dict[str, int] = defaultdict(int)

    for chunk in chunks:
        terms = Counter(tokenize(chunk.embedding_content))
        chunk_term_counts.append(terms)
        length = sum(terms.values()) or 1
        chunk_lengths.append(length)
        for term in terms:
            doc_freq[term] += 1

    total_docs = len(chunks)
    avg_doc_length = sum(chunk_lengths) / max(1, total_docs)
    query_counter = Counter(query_terms)
    scored: list[tuple[float, RetrievalChunk]] = []

    for chunk, terms, doc_length in zip(chunks, chunk_term_counts, chunk_lengths):
        score = 0.0
        for term, query_weight in query_counter.items():
            tf = terms.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + ((total_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5)))
            denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * (doc_length / avg_doc_length))
            score += query_weight * idf * ((tf * (BM25_K1 + 1)) / denominator)

        if score > 0:
            if chunk.symbol_name and chunk.symbol_name.lower() in query_counter:
                score *= 1.15
            if chunk.file_path and Path(chunk.file_path).name.lower() in query.lower():
                score *= 1.10
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    max_score = scored[0][0] if scored else 1.0
    return [_hit_from_chunk(chunk, score / max_score) for score, chunk in scored[:top_k]]
