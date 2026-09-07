"""Offline RAG ablation evaluator.

Run from the repository root:
    python rag/evaluation/evaluate_rag.py --repo-dir C:/path/to/MyRag --knowledge-id <knowledge_id>

The evaluator reads gold incidents, executes retrieval variants, and writes CSV,
JSON, and Markdown result tables. It does not call RCA or mutate production flow.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from issuelayer.intake.schemas import ErrorEvent, make_fingerprint
from rag.domain.schemas import RetrievalHit, RetrievalResult
from rag.indexing.lexical_index import search_lexical, tokenize
from rag.indexing.vector_store import get_vector_store
from rag.retrieval.context_expander import expand_same_file_context
from rag.retrieval.fusion import reciprocal_rank_fusion
from rag.retrieval.incident_parser import build_incident_query
from rag.retrieval.reranker import rerank_hits
from rag.retrieval.verification import verify_hits
from rag.ingestion.file_classifier import retrieval_metadata

DEFAULT_DATASET = REPO_ROOT / "rag" / "evaluation" / "test_events.jsonl"
DEFAULT_RESULTS_DIR = REPO_ROOT / "rag" / "evaluation" / "results"

VARIANTS = (
    "full_rag",
    "bm25_only",
    "semantic_only",
    "bm25_semantic_rrf",
    "no_reranker",
    "no_verification",
    "no_context_expansion",
    "traceback_only",
)

JAVA_FRAME_RE = re.compile(r"at\s+(?P<class>[\w.$]+)\.(?P<function>[\w$<>]+)\((?P<file>[^:()]+)(?::(?P<line>\d+))?\)")
PY_FRAME_RE = re.compile(r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<function>[\w_]+)')


@dataclass(frozen=True)
class VariantConfig:
    lexical: bool = True
    semantic: bool = True
    rrf: bool = True
    rerank: bool = True
    verify: bool = True
    context_expand: bool = True
    traceback_only: bool = False


VARIANT_CONFIGS: dict[str, VariantConfig] = {
    "full_rag": VariantConfig(),
    "bm25_only": VariantConfig(semantic=False, rrf=False, rerank=False, context_expand=False),
    "semantic_only": VariantConfig(lexical=False, rrf=False, rerank=False, context_expand=False),
    "bm25_semantic_rrf": VariantConfig(rerank=False, context_expand=False),
    "no_reranker": VariantConfig(rerank=False),
    "no_verification": VariantConfig(verify=False),
    "no_context_expansion": VariantConfig(context_expand=False),
    "traceback_only": VariantConfig(lexical=False, semantic=False, rrf=False, rerank=False, verify=False, context_expand=False, traceback_only=True),
}


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            required = ["incident_id", "incident_text", "expected_file", "expected_function", "expected_lines"]
            missing = [key for key in required if key not in case]
            if missing:
                raise ValueError(f"Missing required field(s) {missing} at {path}:{line_no}")
            cases.append(case)
    return cases


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def extract_traceback_hints(text: str) -> dict[str, Any]:
    java_matches = list(JAVA_FRAME_RE.finditer(text or ""))
    if java_matches:
        first = java_matches[0]
        class_name = first.group("class") or ""
        return {
            "error_type": _first_non_empty((text or "").splitlines()[0].split(":", 1)[0], "JavaError"),
            "message": _first_non_empty((text or "").splitlines()[0], text),
            "file_path": first.group("file") or "",
            "function_name": first.group("function") or "",
            "class_name": class_name,
            "line_number": int(first.group("line") or 0),
        }

    python_matches = list(PY_FRAME_RE.finditer(text or ""))
    if python_matches:
        last = python_matches[-1]
        error_line = ""
        for line in reversed((text or "").splitlines()):
            if line.strip() and not line.strip().startswith(("File ", "Traceback")):
                error_line = line.strip()
                break
        error_type, _, message = error_line.partition(":")
        return {
            "error_type": _first_non_empty(error_type, "PythonError"),
            "message": _first_non_empty(message, error_line, text),
            "file_path": last.group("file") or "",
            "function_name": last.group("function") or "",
            "class_name": "",
            "line_number": int(last.group("line") or 0),
        }

    return {
        "error_type": "IncidentSymptom",
        "message": (text or "")[:500],
        "file_path": "",
        "function_name": "",
        "class_name": "",
        "line_number": 0,
    }


def build_error_event(case: dict[str, Any]) -> ErrorEvent:
    text = str(case.get("incident_text") or "")
    hints = extract_traceback_hints(text)
    return ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint(hints["error_type"], hints["message"]),
        timestamp=datetime.now(UTC),
        error_type=hints["error_type"],
        message=hints["message"],
        # Preserve the complete incident text whenever a stack frame was
        # parsed. Java traces commonly use a tab before ``at`` (``\tat``),
        # which must not disable stack-aware evaluation.
        traceback=text if hints["file_path"] or "traceback" in case.get("case_type", "") or "Traceback" in text else "",
        file_path=hints["file_path"],
        function_name=hints["function_name"],
        line_number=hints["line_number"],
        class_name=hints["class_name"],
        incident_id=str(case.get("incident_id") or ""),
        description=text,
        short_description=text.splitlines()[0][:200] if text else "",
        repo_url=case.get("repo_url"),
        repo_full_name=case.get("repo_full_name"),
        branch=case.get("branch") or "main",
        source="rag_evaluation",
        raw_description=text,
        issue_category="code" if case.get("expected_artifact_type") == "code" else str(case.get("expected_artifact_type") or ""),
        workflow_action="evaluate_rag",
    )


def _semantic_search(knowledge_id: str, query: str, top_k: int) -> list[RetrievalHit]:
    return get_vector_store().query(knowledge_id=knowledge_id, query=query, top_k=top_k)


def _dedupe_hits(hits: Iterable[RetrievalHit]) -> list[RetrievalHit]:
    seen: set[tuple[str, int, int, str]] = set()
    unique: list[RetrievalHit] = []
    for hit in hits:
        key = (normalize_path(hit.file_path), hit.start_line, hit.end_line, hit.symbol_name.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def retrieve_variant(
    *,
    event: ErrorEvent,
    knowledge_id: str,
    repo_dir: Path,
    variant: str,
    bm25_top_k: int,
    semantic_top_k: int,
    rrf_top_k: int,
    rerank_top_k: int,
    final_top_k: int,
) -> RetrievalResult:
    config = VARIANT_CONFIGS[variant]
    query = build_incident_query(event)
    trace: dict[str, Any] = {"variant": variant}

    if config.traceback_only:
        hit = traceback_hit_from_event(event, knowledge_id)
        hits = [hit] if hit else []
        trace.update({"traceback_candidates": len(hits)})
        return RetrievalResult(query=query, knowledge_id=knowledge_id, hits=hits[:final_top_k], strategy=variant, retrieval_trace=trace)

    lexical_hits: list[RetrievalHit] = []
    semantic_hits: list[RetrievalHit] = []
    failures: list[str] = []

    if config.lexical:
        try:
            lexical_hits = search_lexical(knowledge_id, query, bm25_top_k)
        except Exception as exc:
            failures.append(f"bm25_failed:{exc}")

    if config.semantic:
        try:
            semantic_hits = _semantic_search(knowledge_id, query, semantic_top_k)
        except Exception as exc:
            failures.append(f"semantic_failed:{exc}")

    if config.rrf and config.lexical and config.semantic:
        hits = reciprocal_rank_fusion([lexical_hits, semantic_hits], limit=rrf_top_k)
    else:
        hits = sorted(_dedupe_hits([*lexical_hits, *semantic_hits]), key=lambda item: item.score, reverse=True)[:rrf_top_k]

    if config.rerank:
        hits = rerank_hits(event, query, hits, limit=rerank_top_k)
    else:
        hits = hits[:rerank_top_k]

    if config.verify:
        hits = verify_hits(str(repo_dir), hits)

    if config.context_expand:
        hits = expand_same_file_context(knowledge_id, hits)

    trace.update({
        "bm25_hits": len(lexical_hits),
        "semantic_hits": len(semantic_hits),
        "selected_hits": len(hits),
        "rrf_enabled": config.rrf,
        "rerank_enabled": config.rerank,
        "verification_enabled": config.verify,
        "context_expansion_enabled": config.context_expand,
        "failures": failures,
    })
    return RetrievalResult(query=query, knowledge_id=knowledge_id, hits=hits[:final_top_k], strategy=variant, retrieval_trace=trace)


def traceback_hit_from_event(event: ErrorEvent, knowledge_id: str) -> RetrievalHit | None:
    if not event.file_path:
        return None
    line = max(1, int(event.line_number or 1))
    return RetrievalHit(
        chunk_id=f"traceback:{event.file_path}:{line}",
        file_path=event.file_path,
        start_line=line,
        end_line=line,
        score=1.0,
        source="traceback",
        content_type="traceback_hint",
        language="text",
        symbol_name=event.function_name or "",
        symbol_type="function" if event.function_name else "",
        content=event.traceback or event.description or event.message,
        metadata={"knowledge_id": knowledge_id},
    )


def normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/").lower()


def same_file(actual: str, expected: str) -> bool:
    actual_norm = normalize_path(actual)
    expected_norm = normalize_path(expected)
    return bool(actual_norm and expected_norm and (actual_norm == expected_norm or actual_norm.endswith("/" + expected_norm) or expected_norm.endswith("/" + actual_norm)))


def line_overlap(hit: RetrievalHit, expected_lines: list[int]) -> bool:
    if not expected_lines:
        return False
    start = min(expected_lines)
    end = max(expected_lines)
    return hit.start_line <= end and hit.end_line >= start


def exact_line_hit(hit: RetrievalHit, expected_lines: list[int]) -> bool:
    return any(hit.start_line <= line <= hit.end_line for line in expected_lines)


def important_terms(case: dict[str, Any]) -> set[str]:
    text = " ".join([
        str(case.get("incident_text") or ""),
        str(case.get("expected_function") or ""),
        str(case.get("expected_root_cause") or ""),
    ])
    terms = set(tokenize(text))
    noisy = {"java", "lang", "nullpointerexception", "cannot", "invoke", "because", "return", "value", "com", "vinay", "dto", "service", "controller", "traceback", "most", "recent", "call", "last", "file", "line", "at", "is", "the", "a", "an", "and", "or", "to", "in", "of", "with", "when", "null", "error", "fails"}
    focused = {term for term in terms if len(term) >= 4 and term not in noisy}
    return set(list(focused)[:12])


def evidence_term_hit(hits: list[RetrievalHit], terms: set[str]) -> bool:
    if not terms or not hits:
        return False
    combined = "\n".join(hit.content for hit in hits[:5]).lower()
    matched = [term for term in terms if term.lower() in combined]
    return len(matched) >= max(1, min(3, len(terms)))


def first_expected_rank(hits: list[RetrievalHit], expected_file: str) -> int:
    for index, hit in enumerate(hits, start=1):
        if same_file(hit.file_path, expected_file):
            return index
    return 0


def hit_artifact_metadata(hit: RetrievalHit | None) -> dict[str, Any]:
    if hit is None:
        return {}
    inferred = retrieval_metadata(hit.file_path, hit.language, hit.content_type)
    return {**inferred, **(hit.metadata or {})}


def is_primary_artifact(hit: RetrievalHit | None) -> bool:
    metadata = hit_artifact_metadata(hit)
    return metadata.get("artifact_role") == "primary"


def is_supporting_artifact(hit: RetrievalHit | None) -> bool:
    metadata = hit_artifact_metadata(hit)
    return metadata.get("artifact_role") == "supporting"

def evaluate_case_variant(case: dict[str, Any], result: RetrievalResult, latency_ms: int) -> dict[str, Any]:
    hits = result.hits
    expected_file = str(case["expected_file"])
    expected_function = str(case.get("expected_function") or "")
    expected_lines = [int(line) for line in case.get("expected_lines") or []]
    rank = first_expected_rank(hits, expected_file)
    top_hit = hits[0] if hits else None
    top_metadata = hit_artifact_metadata(top_hit)
    expected_file_hits = [hit for hit in hits if same_file(hit.file_path, expected_file)]
    expected_function_lc = expected_function.lower()
    function_hit = any((hit.symbol_name or "").lower() == expected_function_lc or expected_function_lc in (hit.content or "").lower() for hit in expected_file_hits or hits[:5])
    line_hit = any(line_overlap(hit, expected_lines) for hit in expected_file_hits)
    exact_hit = any(exact_line_hit(hit, expected_lines) for hit in expected_file_hits)
    top_function_hit = bool(top_hit and expected_function_lc and ((top_hit.symbol_name or "").lower() == expected_function_lc or expected_function_lc in (top_hit.content or "").lower()))
    top_line_hit = bool(top_hit and line_overlap(top_hit, expected_lines))
    top_exact_hit = bool(top_hit and exact_line_hit(top_hit, expected_lines))
    fix_target_at_1 = bool(rank == 1 and top_function_hit and top_line_hit)
    terms = important_terms(case)
    error_terms_hit = evidence_term_hit(expected_file_hits or hits[:5], terms)
    evidence_checks = {
        "expected_file_found": rank > 0,
        "expected_function_found_anywhere": function_hit,
        "expected_line_overlap_anywhere": line_hit,
        "top_function_found": top_function_hit,
        "top_line_overlap": top_line_hit,
        "important_error_terms_found": error_terms_hit,
    }
    passed = sum(1 for value in evidence_checks.values() if value)
    confidence = "high" if fix_target_at_1 else "medium" if rank == 1 and (top_function_hit or top_line_hit or function_hit or line_hit) else "low"
    return {
        "incident_id": case["incident_id"],
        "case_type": case.get("case_type", ""),
        "variant": result.strategy,
        "top_file": top_hit.file_path if top_hit else "",
        "top_symbol": top_hit.symbol_name if top_hit else "",
        "top_lines": f"{top_hit.start_line}-{top_hit.end_line}" if top_hit else "",
        "top_score": round(top_hit.score, 4) if top_hit else 0,
        "top_content_type": top_hit.content_type if top_hit else "",
        "top_artifact_type": str(top_metadata.get("artifact_type") or ""),
        "top_artifact_role": str(top_metadata.get("artifact_role") or ""),
        "top_source_priority": top_metadata.get("source_priority", ""),
        "primary_artifact_at_1": bool(is_primary_artifact(top_hit)),
        "supporting_artifact_at_1": bool(is_supporting_artifact(top_hit)),
        "wrong_primary_artifact": bool(top_hit and not is_primary_artifact(top_hit) and rank != 1),
        "expected_file": expected_file,
        "expected_function": expected_function,
        "expected_lines": ";".join(str(line) for line in expected_lines),
        "file_hit_at_1": bool(rank == 1),
        "file_hit_at_3": bool(rank and rank <= 3),
        "file_hit_at_5": bool(rank and rank <= 5),
        "top_rank": rank,
        "mrr": round((1.0 / rank) if rank else 0.0, 4),
        "function_hit": bool(function_hit),
        "line_overlap": bool(line_hit),
        "exact_line_hit": bool(exact_hit),
        "top_function_hit": bool(top_function_hit),
        "top_line_overlap": bool(top_line_hit),
        "top_exact_line_hit": bool(top_exact_hit),
        "fix_target_at_1": bool(fix_target_at_1),
        "evidence_has_error_terms": bool(error_terms_hit),
        "evidence_checks_passed": passed,
        "evidence_checks_total": len(evidence_checks),
        "evidence_support": f"{passed}/{len(evidence_checks)}",
        "retrieval_confidence": confidence,
        "false_high_confidence": bool(confidence == "high" and rank != 1),
        "hit_count": len(hits),
        "latency_ms": latency_ms,
        "failure_reason": "; ".join(result.retrieval_trace.get("failures", [])),
    }


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["variant"], []).append(row)

    summary = []
    bool_metrics = [
        "file_hit_at_1",
        "file_hit_at_3",
        "file_hit_at_5",
        "function_hit",
        "line_overlap",
        "exact_line_hit",
        "top_function_hit",
        "top_line_overlap",
        "top_exact_line_hit",
        "fix_target_at_1",
        "evidence_has_error_terms",
        "false_high_confidence",
        "primary_artifact_at_1",
        "supporting_artifact_at_1",
        "wrong_primary_artifact",
    ]
    for variant, items in grouped.items():
        entry: dict[str, Any] = {"variant": variant, "cases": len(items)}
        for metric in bool_metrics:
            entry[metric] = round(100 * sum(1 for item in items if item[metric]) / max(1, len(items)), 2)
        entry["mrr"] = round(statistics.mean(float(item["mrr"]) for item in items), 4)
        entry["avg_latency_ms"] = round(statistics.mean(int(item["latency_ms"]) for item in items), 2)
        entry["p95_latency_ms"] = percentile([int(item["latency_ms"]) for item in items], 95)
        entry["avg_evidence_checks_passed"] = round(statistics.mean(int(item["evidence_checks_passed"]) for item in items), 2)
        summary.append(entry)
    return sorted(summary, key=lambda item: item["variant"])


def percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "No rows."
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join([header, separator, *body])


def write_markdown(path: Path, detail_rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    summary_columns = ["variant", "cases", "file_hit_at_1", "file_hit_at_3", "file_hit_at_5", "function_hit", "line_overlap", "exact_line_hit", "top_function_hit", "top_line_overlap", "top_exact_line_hit", "fix_target_at_1", "evidence_has_error_terms", "primary_artifact_at_1", "supporting_artifact_at_1", "wrong_primary_artifact", "mrr", "avg_latency_ms", "p95_latency_ms", "false_high_confidence"]
    detail_columns = ["incident_id", "case_type", "variant", "top_file", "top_symbol", "top_lines", "top_content_type", "top_artifact_type", "top_artifact_role", "expected_file", "expected_function", "file_hit_at_1", "file_hit_at_5", "function_hit", "line_overlap", "top_function_hit", "top_line_overlap", "fix_target_at_1", "evidence_support", "retrieval_confidence", "latency_ms", "failure_reason"]
    text = [
        "# RAG Ablation Results",
        "",
        f"Generated at: {datetime.now(UTC).isoformat()}",
        f"Dataset: `{args.dataset}`",
        f"Knowledge ID: `{args.knowledge_id}`",
        f"Repo dir: `{args.repo_dir}`",
        "",
        "## Summary By Variant",
        "",
        markdown_table(summary_rows, summary_columns),
        "",
        "## Detailed Results",
        "",
        markdown_table(detail_rows, detail_columns),
        "",
        "## Metric Notes",
        "",
        "- `file_hit_at_1/3/5`: expected file appears at that rank or better.",
        "- `mrr`: reciprocal rank of expected file; 1.0 means rank 1, 0.0 means not found.",
        "- `line_overlap`: any retrieved expected-file range overlaps the expected line range.",
        "- `top_function_hit/top_line_overlap`: the first result itself matches the expected function or line range.",
        "- `fix_target_at_1`: first result matches expected file, function, and line area.",
        "- `evidence_support`: transparent count of file/function/line/error-term checks passed.",
        "- `false_high_confidence`: high confidence while expected file was not rank 1.",
        "- `top_artifact_type/top_artifact_role`: shows whether the first result is source code, docs, test/generated content, or supporting evidence.",
        "- `wrong_primary_artifact`: top result is not a primary artifact and expected file is not rank 1.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(text), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.dataset))
    variants = args.variants or list(VARIANTS)
    unknown = [variant for variant in variants if variant not in VARIANT_CONFIGS]
    if unknown:
        raise ValueError(f"Unknown variant(s): {unknown}. Valid variants: {sorted(VARIANT_CONFIGS)}")

    rows: list[dict[str, Any]] = []
    failures = 0
    repo_dir = Path(args.repo_dir).resolve()
    if not repo_dir.exists():
        raise ValueError(f"repo-dir does not exist: {repo_dir}")

    print(f"[RAG_EVAL] cases={len(cases)} variants={len(variants)} knowledge_id={args.knowledge_id}")
    for case in cases:
        event = build_error_event(case)
        for variant in variants:
            started = time.perf_counter()
            try:
                result = retrieve_variant(
                    event=event,
                    knowledge_id=args.knowledge_id,
                    repo_dir=repo_dir,
                    variant=variant,
                    bm25_top_k=args.bm25_top_k,
                    semantic_top_k=args.semantic_top_k,
                    rrf_top_k=args.rrf_top_k,
                    rerank_top_k=args.rerank_top_k,
                    final_top_k=args.final_top_k,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                row = evaluate_case_variant(case, result, latency_ms)
            except Exception as exc:
                failures += 1
                latency_ms = int((time.perf_counter() - started) * 1000)
                row = failure_row(case, variant, latency_ms, exc)
            rows.append(row)
            print(f"[RAG_EVAL] case={case['incident_id']} variant={variant} file@1={row['file_hit_at_1']} file@5={row['file_hit_at_5']} function={row['function_hit']} line={row['line_overlap']} latency_ms={row['latency_ms']}")

    summary = aggregate(rows)
    results_dir = Path(args.results_dir)
    write_csv(results_dir / "rag_ablation_results.csv", rows)
    write_csv(results_dir / "rag_ablation_summary.csv", summary)
    write_json(results_dir / "rag_ablation_results.json", {"details": rows, "summary": summary})
    write_markdown(results_dir / "rag_ablation_summary.md", rows, summary, args)

    print("\n[RAG_EVAL] Summary")
    print(markdown_table(summary, ["variant", "cases", "file_hit_at_1", "file_hit_at_5", "function_hit", "line_overlap", "primary_artifact_at_1", "wrong_primary_artifact", "mrr", "avg_latency_ms", "false_high_confidence"]))
    print(f"\n[RAG_EVAL] Results written to {results_dir}")
    return 1 if failures else 0


def failure_row(case: dict[str, Any], variant: str, latency_ms: int, exc: Exception) -> dict[str, Any]:
    return {
        "incident_id": case.get("incident_id", ""),
        "case_type": case.get("case_type", ""),
        "variant": variant,
        "top_file": "",
        "top_symbol": "",
        "top_lines": "",
        "top_score": 0,
        "expected_file": case.get("expected_file", ""),
        "expected_function": case.get("expected_function", ""),
        "expected_lines": ";".join(str(line) for line in case.get("expected_lines", [])),
        "file_hit_at_1": False,
        "file_hit_at_3": False,
        "file_hit_at_5": False,
        "top_rank": 0,
        "mrr": 0.0,
        "function_hit": False,
        "line_overlap": False,
        "exact_line_hit": False,
        "evidence_has_error_terms": False,
        "evidence_checks_passed": 0,
        "evidence_checks_total": 4,
        "evidence_support": "0/4",
        "retrieval_confidence": "low",
        "false_high_confidence": False,
        "hit_count": 0,
        "latency_ms": latency_ms,
        "failure_reason": str(exc),
        "top_content_type": "",
        "top_artifact_type": "",
        "top_artifact_role": "",
        "top_source_priority": "",
        "primary_artifact_at_1": False,
        "supporting_artifact_at_1": False,
        "wrong_primary_artifact": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline RAG ablation tests against a gold incident dataset.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Path to JSONL gold dataset.")
    parser.add_argument("--repo-dir", required=True, help="Local checkout path for the target repo used by verification.")
    parser.add_argument("--knowledge-id", required=True, help="Knowledge id whose lexical/vector indexes should be queried.")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR), help="Directory where result tables are written.")
    parser.add_argument("--variants", nargs="*", choices=sorted(VARIANT_CONFIGS), help="Subset of variants to run.")
    parser.add_argument("--bm25-top-k", type=int, default=50)
    parser.add_argument("--semantic-top-k", type=int, default=50)
    parser.add_argument("--rrf-top-k", type=int, default=30)
    parser.add_argument("--rerank-top-k", type=int, default=12)
    parser.add_argument("--final-top-k", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))





