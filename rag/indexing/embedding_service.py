"""Embedding client abstraction for RAG."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from openai import OpenAI

from config import EMBEDDING_MODEL, OPENAI_API_KEY


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY must be set for RAG embeddings")
    return OpenAI(api_key=OPENAI_API_KEY)


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    items = [text for text in texts]
    if not items:
        return []
    response = _client().embeddings.create(model=EMBEDDING_MODEL, input=items)
    return [item.embedding for item in response.data]
