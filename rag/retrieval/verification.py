"""Lightweight verification for retrieved source evidence."""

from __future__ import annotations

from pathlib import Path

from rag.domain.schemas import RetrievalHit


def verify_hits(repo_dir: str, hits: list[RetrievalHit], min_score: float = 0.05) -> list[RetrievalHit]:
    verified = []
    repo_root = Path(repo_dir).resolve()
    for hit in hits:
        if hit.score < min_score or not hit.file_path:
            continue
        candidate = (repo_root / hit.file_path).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        verified.append(hit)
    return verified
