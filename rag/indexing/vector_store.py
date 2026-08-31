"""Configurable vector-store providers for RAG."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config import CACHE_DIR, PINECONE_API_KEY, PINECONE_INDEX_NAME
from rag.domain.schemas import RetrievalChunk, RetrievalHit
from rag.indexing.embedding_service import embed_texts


class VectorStore(ABC):
    @abstractmethod
    def upsert_chunks(self, chunks: list[RetrievalChunk]) -> None:
        raise NotImplementedError

    @abstractmethod
    def query(self, *, knowledge_id: str, query: str, top_k: int = 50) -> list[RetrievalHit]:
        raise NotImplementedError


class PineconeVectorStore(VectorStore):
    def __init__(self) -> None:
        if not PINECONE_API_KEY or not PINECONE_INDEX_NAME:
            raise ValueError("PINECONE_API_KEY and PINECONE_INDEX_NAME must be set when VECTOR_STORE_PROVIDER=pinecone")
        from pinecone import Pinecone

        self._index = Pinecone(api_key=PINECONE_API_KEY).Index(PINECONE_INDEX_NAME)

    def upsert_chunks(self, chunks: list[RetrievalChunk]) -> None:
        if not chunks:
            return
        vectors = []
        embeddings = embed_texts(chunk.embedding_content for chunk in chunks)
        for chunk, embedding in zip(chunks, embeddings):
            vectors.append({
                "id": chunk.chunk_id,
                "values": embedding,
                "metadata": _metadata_from_chunk(chunk),
            })
        self._index.upsert(vectors=vectors)

    def query(self, *, knowledge_id: str, query: str, top_k: int = 50) -> list[RetrievalHit]:
        vector = embed_texts([query])[0]
        response = self._index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter={"knowledge_id": {"$eq": knowledge_id}},
        )
        matches = response.get("matches", []) if isinstance(response, dict) else response.matches
        hits = []
        for match in matches:
            metadata = dict(match.get("metadata", {}) if isinstance(match, dict) else match.metadata or {})
            score = float(match.get("score", 0.0) if isinstance(match, dict) else match.score or 0.0)
            hits.append(_hit_from_metadata(metadata, score, source="semantic"))
        return hits


class ChromaVectorStore(VectorStore):
    def __init__(self) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ValueError("chromadb must be installed when VECTOR_STORE_PROVIDER=chroma") from exc

        persist_dir = os.getenv("CHROMA_PERSIST_DIR", str(Path(CACHE_DIR) / "chroma"))
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(os.getenv("CHROMA_COLLECTION_NAME", "ams_rag_chunks"))

    def upsert_chunks(self, chunks: list[RetrievalChunk]) -> None:
        if not chunks:
            return
        embeddings = embed_texts(chunk.embedding_content for chunk in chunks)
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=embeddings,
            documents=[chunk.content for chunk in chunks],
            metadatas=[_metadata_from_chunk(chunk, include_content=False) for chunk in chunks],
        )

    def query(self, *, knowledge_id: str, query: str, top_k: int = 50) -> list[RetrievalHit]:
        vector = embed_texts([query])[0]
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=top_k,
            where={"knowledge_id": knowledge_id},
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for document, metadata, distance in zip(documents, metadatas, distances):
            metadata = dict(metadata or {})
            metadata["content"] = document or ""
            score = 1.0 / (1.0 + float(distance or 0.0))
            hits.append(_hit_from_metadata(metadata, score, source="semantic"))
        return hits


class NullVectorStore(VectorStore):
    def upsert_chunks(self, chunks: list[RetrievalChunk]) -> None:
        return None

    def query(self, *, knowledge_id: str, query: str, top_k: int = 50) -> list[RetrievalHit]:
        return []


def _safe_scalar(value: Any) -> str | int | float | bool:
    if isinstance(value, (str, int, float, bool)):
        return value
    if value is None:
        return ""
    return json.dumps(value, default=str)


def _metadata_from_chunk(chunk: RetrievalChunk, include_content: bool = True) -> dict[str, Any]:
    metadata = {
        "chunk_id": chunk.chunk_id,
        "knowledge_id": chunk.knowledge_id,
        "file_path": chunk.file_path,
        "content_type": chunk.content_type,
        "language": chunk.language,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "symbol_name": chunk.symbol_name,
        "symbol_type": chunk.symbol_type,
        "section_path": chunk.section_path,
        "content_hash": chunk.content_hash,
        "parent_file_id": chunk.parent_file_id,
        "parent_symbol_id": chunk.parent_symbol_id,
        "chunk_order": chunk.chunk_order,
        "previous_chunk_id": chunk.previous_chunk_id,
        "next_chunk_id": chunk.next_chunk_id,
        "imports": chunk.imports,
        "called_symbols": chunk.called_symbols,
        "exported_symbols": chunk.exported_symbols,
        "metadata": chunk.metadata,
    }
    if include_content:
        metadata["content"] = chunk.content
    return {key: _safe_scalar(value) for key, value in metadata.items()}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.startswith("["):
        try:
            parsed = json.loads(value)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _hit_from_metadata(metadata: dict, score: float, source: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=str(metadata.get("chunk_id") or metadata.get("content_hash") or ""),
        file_path=str(metadata.get("file_path") or ""),
        start_line=int(metadata.get("start_line") or 1),
        end_line=int(metadata.get("end_line") or metadata.get("start_line") or 1),
        score=score,
        source=source,
        content_type=str(metadata.get("content_type") or ""),
        language=str(metadata.get("language") or "text"),
        symbol_name=str(metadata.get("symbol_name") or ""),
        symbol_type=str(metadata.get("symbol_type") or ""),
        content=str(metadata.get("content") or ""),
        metadata={
            **_json_dict(metadata.get("metadata")),
            "parent_file_id": str(metadata.get("parent_file_id") or ""),
            "parent_symbol_id": str(metadata.get("parent_symbol_id") or ""),
            "chunk_order": int(metadata.get("chunk_order") or 0),
            "previous_chunk_id": str(metadata.get("previous_chunk_id") or ""),
            "next_chunk_id": str(metadata.get("next_chunk_id") or ""),
            "imports": _json_list(metadata.get("imports")),
            "called_symbols": _json_list(metadata.get("called_symbols")),
            "exported_symbols": _json_list(metadata.get("exported_symbols")),
            "content_hash": str(metadata.get("content_hash") or ""),
        },
    )


def get_vector_store() -> VectorStore:
    provider = os.getenv("VECTOR_STORE_PROVIDER", "pinecone").strip().lower()
    if provider == "pinecone":
        return PineconeVectorStore()
    if provider == "chroma":
        return ChromaVectorStore()
    if provider in {"none", "disabled", "off"}:
        return NullVectorStore()
    raise ValueError(f"Unsupported VECTOR_STORE_PROVIDER: {provider}")
