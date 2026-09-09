"""Request-local, source-owned evidence for RCA and bounded follow-up reads."""

import hashlib
from pathlib import Path
from threading import Lock

from agents.l3_rca.validation import SourceCitation, _source_path


class SourceEvidence:
    def __init__(self, repo_dir: str, knowledge_id: str, max_chars: int = 24000):
        self.root = Path(repo_dir).resolve()
        self.knowledge_id = knowledge_id
        self.max_chars = max_chars
        self.records: dict[str, dict] = {}
        self.errors: list[dict] = []
        self.location_errors: list[str] = []
        self._lock = Lock()

    def read(self, file_path: str, start_line: int, end_line: int) -> dict:
        with self._lock:
            return self._read(file_path, start_line, end_line)

    def _read(self, file_path: str, start_line: int, end_line: int) -> dict:
        try:
            path = _source_path(self.root, file_path)
            if path.stat().st_size > 2_000_000:
                raise ValueError("source file exceeds RCA read limit")
            raw = path.read_bytes()
            lines = raw.decode("utf-8").splitlines()
            if start_line < 1 or end_line < start_line or start_line > len(lines):
                raise ValueError("invalid source range")
            end_line = min(end_line, len(lines), start_line + 159)
            excerpt = "\n".join(lines[start_line - 1:end_line])
            if not excerpt.strip():
                raise ValueError("empty source range")
            relative = path.relative_to(self.root).as_posix()
            digest = hashlib.sha256(raw).hexdigest()
            identity = f"{self.knowledge_id}:{relative}:{digest}:{start_line}:{end_line}"
            evidence_id = "src_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            if evidence_id not in self.records:
                if len(self.records) >= 20 or sum(len(r["excerpt"]) for r in self.records.values()) + len(excerpt) > self.max_chars:
                    raise ValueError("source evidence budget exhausted; request a smaller range")
                self.records[evidence_id] = dict(
                    evidence_id=evidence_id, file_path=relative, start_line=start_line,
                    end_line=end_line, excerpt=excerpt, file_sha256=digest,
                    knowledge_id=self.knowledge_id,
                )
            return dict(self.records[evidence_id])
        except (OSError, ValueError, UnicodeError) as exc:
            failure = {"file_path": file_path, "start_line": start_line,
                       "end_line": end_line, "error_type": type(exc).__name__}
            self.errors.append(failure)
            return {"error": "Source evidence unavailable or outside read budget", **failure}

    def bind(self, result) -> list[str]:
        """Hydrate citations from known IDs; source changes invalidate the evidence."""
        errors = []
        self.location_errors = []
        ids = list(dict.fromkeys(result.analysis_facts.evidence_ids))
        for location in result.analysis_facts.defect_locations:
            ids.extend(eid for eid in location.evidence_ids if eid not in ids)
        citations = []
        verified = {}
        for evidence_id in ids:
            record = self.records.get(evidence_id)
            if not record:
                errors.append(f"Unknown source evidence ID: {evidence_id}")
                continue
            try:
                path = _source_path(self.root, record["file_path"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != record["file_sha256"]:
                    raise ValueError("source changed after evidence collection")
            except (OSError, ValueError):
                errors.append(f"Source snapshot changed or unavailable: {evidence_id}")
                continue
            verified[evidence_id] = record
            citations.append(SourceCitation(**{k: record[k] for k in
                                              ("file_path", "start_line", "end_line", "excerpt")}))
        result.analysis_facts.citations = citations
        result.buggy_lines = []
        for location in result.analysis_facts.defect_locations:
            if not any(
                eid in verified and verified[eid]["file_path"] == result.buggy_file.replace("\\", "/")
                and verified[eid]["start_line"] <= location.line <= verified[eid]["end_line"]
                for eid in location.evidence_ids
            ):
                self.location_errors.append(f"Defect line {location.line} lacks supporting source evidence")
            else:
                result.buggy_lines.append(location.line)
        result.buggy_lines = sorted(set(result.buggy_lines))
        if not citations:
            errors.append("No verified source evidence references were supplied")
        return errors


def seed_source_evidence(store: SourceEvidence, event, context: dict) -> None:
    """Use retrieval for locations, but read evidence from the current checkout."""
    candidates = list((context.get("rag_result") or {}).get("hits") or [])[:6]
    primary = (context.get("evidence_bundle") or {}).get("primary_candidate")
    if primary:
        candidates.insert(0, primary)
    candidates.extend(((context.get("evidence_bundle") or {}).get("traceback_candidates") or [])[:3])
    if event.file_path and event.line_number:
        candidates.append(dict(file_path=event.file_path,
                                 start_line=max(1, event.line_number - 12),
                                 end_line=event.line_number + 12))
    for candidate in candidates:
        start = candidate.get("start_line") or 1
        end = candidate.get("end_line") or start
        if start == end:
            start, end = max(1, start - 12), end + 12
        store.read(candidate.get("file_path") or "", start, end)
