"""Build retrieval chunks from existing scanner and parser output."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from models import FileInfo
from rag.domain.schemas import RetrievalChunk
from rag.ingestion.file_classifier import classify_file, is_retrievable_file, retrieval_metadata


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _read_lines(file_info: FileInfo) -> list[str]:
    try:
        with open(file_info.absolute_path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.readlines()
    except OSError:
        return []


def _line_range(lines: list[str], start_line: int, end_line: int) -> str:
    start = max(1, start_line)
    end = max(start, end_line)
    return "".join(lines[start - 1:end])


def _embedding_text(file_path: str, content_type: str, language: str, symbol: str, text: str) -> str:
    header = f"file={file_path}\ntype={content_type}\nlanguage={language}"
    if symbol:
        header += f"\nsymbol={symbol}"
    return f"{header}\n\n{text}"


def _import_names(file_info: FileInfo) -> list[str]:
    names = []
    for item in file_info.imports:
        value = item.module or item.raw
        if value:
            names.append(value)
    return names


def _exported_names(file_info: FileInfo) -> list[str]:
    return [item.name for item in [*file_info.exported_functions, *file_info.exported_classes] if item.name]


def _make_chunk(
    *,
    knowledge_id: str,
    file_info: FileInfo,
    content_type: str,
    start_line: int,
    end_line: int,
    content: str,
    chunk_order: int,
    symbol_name: str = "",
    symbol_type: str = "",
    section_path: str = "",
    called_symbols: list[str] | None = None,
) -> RetrievalChunk | None:
    text = content.strip()
    if not text:
        return None

    symbol_label = symbol_name or section_path
    content_hash = _hash_text(text)
    chunk_id = _stable_id(knowledge_id, file_info.path, start_line, end_line, symbol_label or content_type, content_hash)
    parent_file_id = _stable_id(knowledge_id, "file", file_info.path)
    parent_symbol_id = _stable_id(knowledge_id, file_info.path, symbol_name) if symbol_name else ""
    embedding_content = _embedding_text(
        file_info.path,
        content_type,
        file_info.language or "text",
        symbol_label,
        text,
    )
    return RetrievalChunk(
        chunk_id=chunk_id,
        knowledge_id=knowledge_id,
        file_path=file_info.path,
        content_type=content_type,
        language=file_info.language or "text",
        start_line=start_line,
        end_line=end_line,
        symbol_name=symbol_name,
        symbol_type=symbol_type,
        section_path=section_path,
        content=text,
        embedding_content=embedding_content,
        content_hash=content_hash,
        parent_file_id=parent_file_id,
        parent_symbol_id=parent_symbol_id,
        chunk_order=chunk_order,
        imports=_import_names(file_info),
        called_symbols=called_symbols or [],
        exported_symbols=_exported_names(file_info),
        metadata={
            "total_lines": file_info.total_lines,
            "package": file_info.package or "",
            "file_summary": file_info.summary or "",
            "file_purpose": file_info.purpose or "",
            **retrieval_metadata(file_info.path, file_info.language or "text", content_type),
        },
    )


def _link_neighbors(chunks: list[RetrievalChunk]) -> list[RetrievalChunk]:
    by_file: dict[str, list[RetrievalChunk]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk.file_path, []).append(chunk)

    for file_chunks in by_file.values():
        file_chunks.sort(key=lambda chunk: (chunk.start_line, chunk.end_line, chunk.chunk_order))
        for index, chunk in enumerate(file_chunks):
            chunk.chunk_order = index
            chunk.previous_chunk_id = file_chunks[index - 1].chunk_id if index > 0 else ""
            chunk.next_chunk_id = file_chunks[index + 1].chunk_id if index < len(file_chunks) - 1 else ""
    return chunks


def _split_text_lines(lines: list[str], max_chars: int, start_line_offset: int = 0) -> Iterable[tuple[int, int, str]]:
    current: list[str] = []
    current_start = start_line_offset + 1
    for index, line in enumerate(lines, start=start_line_offset + 1):
        if len(line) > max_chars:
            if current:
                yield current_start, index - 1, "".join(current)
                current = []
            for offset in range(0, len(line), max_chars):
                yield index, index, line[offset:offset + max_chars]
            current_start = index + 1
            continue
        if current and len("".join(current)) + len(line) > max_chars:
            yield current_start, index - 1, "".join(current)
            current = []
            current_start = index
        current.append(line)
    if current:
        yield current_start, current_start + len(current) - 1, "".join(current)

def _markdown_sections(lines: list[str], max_chars: int) -> Iterable[tuple[int, int, str, str]]:
    heading = ""
    current: list[str] = []
    current_start = 1
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")
    for index, line in enumerate(lines, start=1):
        match = heading_pattern.match(line.strip())
        if match and current:
            yield current_start, index - 1, heading, "".join(current)
            current = []
            current_start = index
        if match:
            heading = match.group(2).strip()
        current.append(line)
        if len("".join(current)) > max_chars:
            yield current_start, index, heading, "".join(current)
            current = []
            current_start = index + 1
    if current:
        yield current_start, current_start + len(current) - 1, heading, "".join(current)


def build_retrieval_chunks(file_info: FileInfo, knowledge_id: str, max_chars: int = 6000) -> list[RetrievalChunk]:
    if not is_retrievable_file(file_info.path, file_info.language):
        return []

    lines = _read_lines(file_info)
    if not lines:
        return []

    content_type = classify_file(file_info.path, file_info.language)
    chunks: list[RetrievalChunk] = []

    if content_type == "code" and file_info.functions:
        chunk_order = 0
        for fn in file_info.functions:
            symbol_lines = lines[max(0, fn.start_line - 1):fn.end_line]
            symbol_text = "".join(symbol_lines)
            ranges = [(fn.start_line, fn.end_line, symbol_text)]
            if len(symbol_text) > max_chars:
                ranges = list(_split_text_lines(symbol_lines, max_chars=max_chars, start_line_offset=fn.start_line - 1))
            for start, end, text in ranges:
                chunk = _make_chunk(
                    knowledge_id=knowledge_id,
                    file_info=file_info,
                    content_type="code_symbol",
                    start_line=start,
                    end_line=end,
                    content=text,
                    chunk_order=chunk_order,
                    symbol_name=fn.name,
                    symbol_type="method" if fn.is_method else "function",
                    called_symbols=fn.calls,
                )
                if chunk:
                    chunks.append(chunk)
                    chunk_order += 1
        return _link_neighbors(chunks)

    if content_type == "documentation":
        for order, (start, end, heading, text) in enumerate(_markdown_sections(lines, max_chars=max_chars)):
            chunk = _make_chunk(
                knowledge_id=knowledge_id,
                file_info=file_info,
                content_type=content_type,
                start_line=start,
                end_line=end,
                content=text,
                chunk_order=order,
                section_path=heading,
            )
            if chunk:
                chunks.append(chunk)
        return _link_neighbors(chunks)

    for order, (start, end, text) in enumerate(_split_text_lines(lines, max_chars=max_chars)):
        chunk = _make_chunk(
            knowledge_id=knowledge_id,
            file_info=file_info,
            content_type=content_type,
            start_line=start,
            end_line=end,
            content=text,
            chunk_order=order,
        )
        if chunk:
            chunks.append(chunk)
    return _link_neighbors(chunks)
