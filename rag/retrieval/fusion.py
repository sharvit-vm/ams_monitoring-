"""Merge lexical and semantic retrieval hits deterministically."""

from __future__ import annotations

from rag.domain.schemas import RetrievalHit


def reciprocal_rank_fusion(hit_groups: list[list[RetrievalHit]], limit: int = 8, k: int = 60) -> list[RetrievalHit]:
    by_key: dict[tuple[str, int, int], RetrievalHit] = {}
    scores: dict[tuple[str, int, int], float] = {}

    for hits in hit_groups:
        for rank, hit in enumerate(hits, start=1):
            key = (hit.file_path, hit.start_line, hit.end_line)
            scores[key] = scores.get(key, 0.0) + (1.0 / (k + rank))
            existing = by_key.get(key)
            by_key[key] = hit if existing is None or hit.score >= existing.score else existing
            sources = set(filter(None, [by_key[key].source, hit.source, existing.source if existing else ""]))
            by_key[key].source = "+".join(sorted(sources))

    ranked = sorted(by_key.items(), key=lambda item: scores[item[0]], reverse=True)
    max_score = scores[ranked[0][0]] if ranked else 1.0
    results = []
    for key, hit in ranked[:limit]:
        hit.score = scores[key] / max_score
        results.append(hit)
    return results
