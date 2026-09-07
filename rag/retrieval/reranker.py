"""Deterministic reranker for incident retrieval evidence.

The reranker is artifact-aware: it retrieves broadly, but chooses primary
candidates based on the RCA route. For L3/code incidents, source-code symbols
must outrank docs that merely describe or repeat the incident.
"""

from __future__ import annotations

import re
from pathlib import Path

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import RetrievalHit
from rag.indexing.lexical_index import tokenize
from rag.ingestion.file_classifier import retrieval_metadata
from rag.retrieval.incident_parser import parse_traceback_frames

CODE_TYPES = {"code", "code_symbol"}
SUPPORTING_TYPES = {"documentation", "configuration", "dependency"}
CODE_SOURCE_ROOTS = {"src", "app", "lib", "service", "services", "controller", "controllers", "repository", "repositories"}


def _token_overlap(query_terms: set[str], value: str) -> float:
    terms = set(tokenize(value))
    if not query_terms or not terms:
        return 0.0
    return len(query_terms & terms) / max(1, len(query_terms))


def _path_tokens(file_path: str) -> set[str]:
    path = Path((file_path or "").replace("\\", "/"))
    tokens = set()
    for part in path.parts:
        tokens.update(tokenize(part))
    return tokens


def _is_code_intent(event: ErrorEvent) -> bool:
    values = " ".join([
        str(event.issue_category or ""),
        str(event.workflow_action or ""),
        str(event.error_type or ""),
        str(event.traceback or ""),
        str(event.file_path or ""),
        str(event.function_name or ""),
    ]).lower()
    return bool(event.is_code_issue or "code" in values or event.file_path or event.function_name or "traceback" in values or "exception" in values)


def _metadata(hit: RetrievalHit) -> dict:
    inferred = retrieval_metadata(hit.file_path, hit.language, hit.content_type)
    return {**inferred, **(hit.metadata or {})}


def _basename_matches_hint(hit: RetrievalHit, file_hint: str) -> bool:
    if not file_hint:
        return False
    return Path(hit.file_path.replace("\\", "/")).name.lower() == file_hint


def _path_contains_source_root(hit: RetrievalHit) -> bool:
    return bool(_path_tokens(hit.file_path) & CODE_SOURCE_ROOTS)


def _called_symbols(hit: RetrievalHit) -> set[str]:
    """Combine indexed call metadata with calls visible in verified content."""
    symbols = {str(value).lower() for value in (hit.metadata.get("called_symbols") or [])}
    symbols.update(
        match.lower()
        for match in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", hit.content or "")
    )
    return symbols


def rerank_hits(event: ErrorEvent, query: str, hits: list[RetrievalHit], limit: int = 12) -> list[RetrievalHit]:
    query_terms = set(tokenize(query))
    file_hint = Path(event.file_path.replace("\\", "/")).name.lower() if event.file_path else ""
    function_hint = (event.function_name or "").lower()
    class_hint = Path(event.file_path.replace("\\", "/")).stem.lower() if event.file_path else (event.class_name or "").split(".")[-1].lower()
    message_terms = set(tokenize(" ".join([event.error_type, event.message, event.short_description or ""])))
    code_intent = _is_code_intent(event)
    frames = parse_traceback_frames(event.traceback)
    frame_by_function = {frame.function_name.lower(): frame for frame in frames if frame.function_name}
    application_frames = [frame for frame in frames if not frame.framework_frame]
    first_application_function = (
        application_frames[0].function_name.lower() if application_frames else ""
    )
    proven_upstream_symbols = {
        hit.symbol_name.lower()
        for hit in hits
        if hit.symbol_name and first_application_function in _called_symbols(hit)
    }

    scored: list[tuple[float, RetrievalHit]] = []
    for hit in hits:
        metadata = _metadata(hit)
        artifact_type = str(metadata.get("artifact_type") or "")
        artifact_role = str(metadata.get("artifact_role") or "supporting")
        source_priority = float(metadata.get("source_priority") or 0.4)

        score = hit.score
        score += 0.22 * _token_overlap(query_terms, hit.content)
        score += 0.14 * _token_overlap(message_terms, hit.content)
        score += 0.05 * _token_overlap(query_terms, hit.file_path)

        if code_intent:
            if hit.content_type == "code_symbol":
                score += 0.28
            elif hit.content_type == "code":
                score += 0.16
            elif artifact_type in {"documentation", "runbook_or_doc"}:
                score -= 0.05
            elif artifact_type == "incident_note":
                score -= 0.18
            elif artifact_type in {"code_test", "generated"}:
                score -= 0.25

            if artifact_role == "primary":
                score += 0.12 * source_priority
            else:
                score -= 0.08 * (1.0 - source_priority)

            if _path_contains_source_root(hit) and artifact_type == "code_source":
                score += 0.08
        else:
            if artifact_type in {"runbook_or_doc", "documentation", "configuration", "dependency"}:
                score += 0.12
            if artifact_type == "incident_note":
                score += 0.03

        if file_hint and _basename_matches_hint(hit, file_hint):
            score += 0.45 if code_intent and artifact_type == "code_source" else 0.20
        if class_hint and class_hint in Path(hit.file_path.replace("\\", "/")).stem.lower():
            score += 0.12
        if function_hint and hit.symbol_name.lower() == function_hint:
            score += 0.40 if code_intent else 0.25
        elif function_hint and function_hint in (hit.content or "").lower() and hit.content_type in CODE_TYPES:
            score += 0.20

        frame = frame_by_function.get((hit.symbol_name or "").lower())
        if frame:
            score += 0.20
            if hit.start_line <= frame.line_number <= hit.end_line:
                score += 0.18
            hit.metadata = {**hit.metadata, "traceback_frame_index": frame.frame_index, "traceback_frame_match": True}

        called_symbols = _called_symbols(hit)
        caller_frame = next((candidate for name, candidate in frame_by_function.items() if name in called_symbols), None)
        if caller_frame and code_intent:
            score += 0.16
            hit.metadata = {**hit.metadata, "traceback_caller_candidate": True, "caller_frame_index": caller_frame.frame_index}

        if (
            first_application_function
            and first_application_function in frame_by_function
            and frame_by_function[first_application_function].framework_frame is False
            and first_application_function != (hit.symbol_name or "").lower()
            and (hit.symbol_name or "").lower() in proven_upstream_symbols
        ):
            # A later application frame is a stronger fix candidate when its
            # indexed code explicitly calls the first application frame.
            score += 0.55
            hit.metadata = {**hit.metadata, "traceback_upstream_candidate": True}

        if (
            first_application_function
            and (hit.symbol_name or "").lower() == first_application_function
            and len(application_frames) > 1
            and proven_upstream_symbols
        ):
            # Keep the observed failure site, but avoid treating it as the
            # fix site when verified upstream candidates exist.
            score -= 0.12
            hit.metadata = {**hit.metadata, "traceback_leaf_candidate": True}

        if event.error_type and event.error_type.lower() in hit.content.lower():
            score += 0.04
        if event.message and _token_overlap(set(tokenize(event.message)), hit.content) >= 0.25:
            score += 0.06

        raw_score = score
        normalized = max(0.0, min(raw_score, 2.5)) / 2.5
        hit.score = round(normalized, 4)
        reranker_metadata = {**metadata, **hit.metadata}
        hit.metadata = {
            **reranker_metadata,
            "reranker": "artifact_aware_v1",
            "reranker_inputs": {
                "code_intent": code_intent,
                "file_hint": file_hint,
                "function_hint": function_hint,
                "artifact_type": artifact_type,
                "artifact_role": artifact_role,
                "source_priority": source_priority,
            },
        }
        # Rank by the unbounded score. Normalization is only for external
        # reporting; clamping must never turn distinct candidates into a tie.
        scored.append((raw_score, hit))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [hit for _, hit in scored[:limit]]
