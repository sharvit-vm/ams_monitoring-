"""Hybrid RAG retrieval used by L3 RCA when traceback location is weak."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import RetrievalResult
from rag.indexing.lexical_index import search_lexical
from rag.indexing.vector_store import get_vector_store
from rag.retrieval.context_expander import expand_same_file_context
from rag.retrieval.fusion import reciprocal_rank_fusion
from rag.retrieval.incident_parser import build_incident_query
from rag.retrieval.reranker import rerank_hits
from rag.retrieval.verification import verify_hits

RAG_BM25_TOP_K = int(os.getenv("RAG_BM25_TOP_K", "50"))
RAG_SEMANTIC_TOP_K = int(os.getenv("RAG_SEMANTIC_TOP_K", "50"))
RAG_RRF_TOP_K = int(os.getenv("RAG_RRF_TOP_K", "30"))
RAG_RERANK_TOP_K = int(os.getenv("RAG_RERANK_TOP_K", "12"))
RAG_RETRIEVAL_TOP_K = int(os.getenv("RAG_RETRIEVAL_TOP_K", "8"))
RRF_K = int(os.getenv("RAG_RRF_K", "60"))


def _semantic_search(knowledge_id: str, query: str, top_k: int) -> list:
    provider = os.getenv("VECTOR_STORE_PROVIDER", "pinecone").strip().lower()
    if provider in {"none", "disabled", "off"}:
        return []
    try:
        return get_vector_store().query(knowledge_id=knowledge_id, query=query, top_k=top_k)
    except Exception as exc:
        print(f"[RAG] Semantic retrieval skipped; provider={provider}, error={exc}")
        return []


def retrieve_incident_context(event: ErrorEvent, knowledge_id: str, repo_dir: str, top_k: int = RAG_RETRIEVAL_TOP_K) -> RetrievalResult:
    query = build_incident_query(event)
    provider = os.getenv("VECTOR_STORE_PROVIDER", "pinecone").strip().lower()

    print(
        "[RAG] Hybrid retrieval started; "
        f"knowledge_id={knowledge_id}, bm25_top_k={RAG_BM25_TOP_K}, "
        f"semantic_top_k={RAG_SEMANTIC_TOP_K}, provider={provider}"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        lexical_future = executor.submit(search_lexical, knowledge_id, query, RAG_BM25_TOP_K)
        semantic_future = executor.submit(_semantic_search, knowledge_id, query, RAG_SEMANTIC_TOP_K)
        lexical_hits = lexical_future.result()
        semantic_hits = semantic_future.result()

    fused_hits = reciprocal_rank_fusion([lexical_hits, semantic_hits], limit=RAG_RRF_TOP_K, k=RRF_K)
    reranked_hits = rerank_hits(event, query, fused_hits, limit=RAG_RERANK_TOP_K)
    verified_hits = expand_same_file_context(knowledge_id, verify_hits(repo_dir, reranked_hits))[:top_k]

    trace = {
        "bm25_top_k_requested": RAG_BM25_TOP_K,
        "semantic_top_k_requested": RAG_SEMANTIC_TOP_K,
        "rrf_top_k_requested": RAG_RRF_TOP_K,
        "rerank_top_k_requested": RAG_RERANK_TOP_K,
        "final_top_k_requested": top_k,
        "bm25_hits": len(lexical_hits),
        "semantic_hits": len(semantic_hits),
        "rrf_hits": len(fused_hits),
        "reranked_hits": len(reranked_hits),
        "verified_hits": len(verified_hits),
        "vector_provider": provider,
        "fusion": "reciprocal_rank_fusion",
        "reranker": "deterministic_v1",
        "verification": "file_exists_and_inside_repo",
        "context_expansion": "same_file_previous_next_chunks",
    }
    summary = (
        f"Hybrid RAG selected {len(verified_hits)} verified hit(s); "
        f"bm25={len(lexical_hits)}, semantic={len(semantic_hits)}, "
        f"rrf={len(fused_hits)}, reranked={len(reranked_hits)}, provider={provider}"
    )
    print(f"[RAG] {summary}")
    return RetrievalResult(
        query=query,
        knowledge_id=knowledge_id,
        hits=verified_hits,
        strategy="bm25_semantic_rrf_rerank_verify",
        evidence_summary=summary,
        retrieval_trace=trace,
    )


def format_retrieval_result(result: RetrievalResult, max_chars: int = 12000) -> str:
    if not result.hits:
        return result.evidence_summary or "No RAG evidence found."

    sections = [result.evidence_summary, f"RETRIEVAL TRACE\n{result.retrieval_trace}"]
    used = sum(len(section) for section in sections)
    for index, hit in enumerate(result.hits, start=1):
        block = (
            f"RAG HIT {index}\n"
            f"source={hit.source} score={hit.score:.2f} type={hit.content_type}\n"
            f"file={hit.file_path} lines={hit.start_line}-{hit.end_line}\n"
            f"symbol={hit.symbol_name or 'n/a'}\n"
            f"neighbors previous={hit.metadata.get('previous_chunk_id') or 'n/a'} next={hit.metadata.get('next_chunk_id') or 'n/a'}\n"
            f"called_symbols={hit.metadata.get('called_symbols') or []}\n"
            f"same_file_neighbors={hit.metadata.get('same_file_neighbor_chunks') or []}\n"
            f"{hit.content}"
        )
        if used + len(block) > max_chars:
            sections.append("...[rag evidence truncated]")
            break
        sections.append(block)
        used += len(block)
    return "\n\n---\n\n".join(sections)
