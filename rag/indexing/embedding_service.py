"""Embedding client abstraction for RAG."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

from openai import OpenAI

from config import EMBEDDING_MODEL, OPENAI_API_KEY

RAG_EMBEDDING_MAX_CHARS = int(os.getenv("RAG_EMBEDDING_MAX_CHARS", "8000"))


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY must be set for RAG embeddings")
    return OpenAI(api_key=OPENAI_API_KEY)


def _embedding_safe_text(text: str) -> str:
    if len(text) <= RAG_EMBEDDING_MAX_CHARS:
        return text
    return text[:RAG_EMBEDDING_MAX_CHARS]


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    items = [_embedding_safe_text(text) for text in texts]
    if not items:
        return []
    response = _client().embeddings.create(model=EMBEDDING_MODEL, input=items)
    return [item.embedding for item in response.data]
