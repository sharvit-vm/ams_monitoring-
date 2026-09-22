"""Shared RAG data contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalChunk(BaseModel):
    chunk_id: str
    knowledge_id: str
    file_path: str
    content_type: str
    language: str = "text"
    start_line: int = 1
    end_line: int = 1
    symbol_name: str = ""
    symbol_type: str = ""
    section_path: str = ""
    content: str
    embedding_content: str
    content_hash: str

    # Context stitching metadata. These fields let a retrieved chunk reconnect
    # to its file, neighboring chunks, and parsed symbol graph without changing
    # the existing ErrorEvent contract.
    parent_file_id: str = ""
    parent_symbol_id: str = ""
    chunk_order: int = 0
    previous_chunk_id: str = ""
    next_chunk_id: str = ""
    imports: list[str] = Field(default_factory=list)
    called_symbols: list[str] = Field(default_factory=list)
    exported_symbols: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalHit(BaseModel):
    chunk_id: str
    file_path: str
    start_line: int = 1
    end_line: int = 1
    score: float = 0.0
    source: str = ""
    content_type: str = ""
    language: str = "text"
    symbol_name: str = ""
    symbol_type: str = ""
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    query: str
    knowledge_id: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    strategy: str = "hybrid"
    evidence_summary: str = ""
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)


class EvidenceCandidate(BaseModel):
    source: str
    file_path: str = ""
    symbol_name: str = ""
    start_line: int = 0
    end_line: int = 0
    score: float = 0.0
    reason: str = ""
    role: str = "candidate"
    frame_index: int | None = None


class TracebackFrame(BaseModel):
    """Parsed stack frame retained as provenance, not as a fix decision."""

    frame_index: int
    file_hint: str = ""
    file_path: str = ""
    function_name: str = ""
    class_name: str = ""
    line_number: int = 0
    raw: str = ""
    framework_frame: bool = False


class EvidenceBundle(BaseModel):
    knowledge_id: str
    primary_candidate: EvidenceCandidate | None = None
    traceback_candidate: EvidenceCandidate | None = None
    traceback_frames: list[TracebackFrame] = Field(default_factory=list)
    traceback_candidates: list[EvidenceCandidate] = Field(default_factory=list)
    rag_candidates: list[EvidenceCandidate] = Field(default_factory=list)
    candidate_graph_contexts: list[dict[str, Any]] = Field(default_factory=list)
    same_file_context: list[dict[str, Any]] = Field(default_factory=list)
    graph_context: dict[str, Any] = Field(default_factory=dict)
    doc_context: list[dict[str, Any]] = Field(default_factory=list)
    config_context: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
    confidence_inputs: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: str = ""
