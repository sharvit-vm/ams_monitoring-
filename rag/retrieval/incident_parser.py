"""Build retrieval queries from the normalized incident contract."""

from __future__ import annotations

import os
import re
from pathlib import Path

from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import TracebackFrame


JAVA_FRAME_RE = re.compile(r"^\s*at\s+(?P<qualified>[\w.$<>/]+)\((?P<file>[^():]+):(?P<line>\d+)\)")
PYTHON_FRAME_RE = re.compile(r"^\s*File\s+[\"'](?P<file>.+?)[\"'],\s+line\s+(?P<line>\d+),\s+in\s+(?P<function>\S+)")
JS_FRAME_RE = re.compile(r"^\s*at\s+(?:(?P<function>[^()]+)\s+)?\(?(?P<file>[^():]+):(?P<line>\d+)(?::\d+)?\)?")
GENERIC_FRAME_RE = re.compile(r"^\s*(?P<file>[^:\s()]+\.(?:py|java|js|jsx|ts|tsx|go)):(?P<line>\d+)")


def _framework_frame(class_name: str, file_hint: str) -> bool:
    value = f"{class_name} {file_hint}".lower()
    return value.startswith(("java.", "javax.", "jdk.", "sun.", "org.junit.", "junit.", "org.apache.tools.", "com.sun."))


def parse_traceback_frames(traceback: str) -> list[TracebackFrame]:
    frames: list[TracebackFrame] = []
    for raw in (traceback or "").splitlines():
        match = JAVA_FRAME_RE.match(raw)
        if match:
            qualified = match.group("qualified")
            class_name, _, function_name = qualified.rpartition(".")
            file_hint = match.group("file")
            frames.append(TracebackFrame(
                frame_index=len(frames), file_hint=file_hint,
                function_name=function_name, class_name=class_name,
                line_number=int(match.group("line")), raw=raw.strip(),
                framework_frame=_framework_frame(class_name, file_hint),
            ))
            continue
        match = PYTHON_FRAME_RE.match(raw)
        if match:
            frames.append(TracebackFrame(
                frame_index=len(frames), file_hint=match.group("file"),
                function_name=match.group("function"),
                line_number=int(match.group("line")), raw=raw.strip(),
            ))
            continue
        match = JS_FRAME_RE.match(raw)
        if match:
            frames.append(TracebackFrame(
                frame_index=len(frames), file_hint=match.group("file"),
                function_name=(match.group("function") or "").strip(),
                line_number=int(match.group("line")), raw=raw.strip(),
            ))
            continue
        match = GENERIC_FRAME_RE.match(raw)
        if match:
            frames.append(TracebackFrame(
                frame_index=len(frames), file_hint=match.group("file"),
                line_number=int(match.group("line")), raw=raw.strip(),
            ))
    return frames


def resolve_traceback_frames(traceback: str, repo_dir: str) -> list[TracebackFrame]:
    """Resolve frame hints against the checked-out repository."""
    frames = parse_traceback_frames(traceback)
    if not repo_dir or not os.path.isdir(repo_dir) or not frames:
        return frames
    root = Path(repo_dir).resolve()
    files_by_name: dict[str, list[str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        files_by_name.setdefault(path.name.lower(), []).append(relative)

    for frame in frames:
        hint = frame.file_hint.replace("\\", "/").lstrip("./")
        direct = (root / hint).resolve()
        try:
            direct.relative_to(root)
        except ValueError:
            direct = Path()
        if direct.is_file():
            frame.file_path = direct.relative_to(root).as_posix()
            continue
        matches = files_by_name.get(Path(hint).name.lower(), [])
        class_suffix = frame.class_name.replace(".", "/") if frame.class_name else ""
        qualified_matches = [candidate for candidate in matches if class_suffix and class_suffix in candidate.replace("\\", "/")]
        frame.file_path = qualified_matches[0] if qualified_matches else matches[0] if len(matches) == 1 else ""
    return frames


def build_incident_query(event: ErrorEvent) -> str:
    parts = [
        event.error_type,
        event.message,
        event.short_description or "",
        event.description or "",
        event.raw_description or "",
        event.traceback or "",
        event.file_path or "",
        event.function_name or "",
        event.class_name or "",
        " ".join(event.labels or []),
        " ".join(event.components or []),
    ]
    return "\n".join(part.strip() for part in parts if part and str(part).strip())


def has_strong_traceback_location(event: ErrorEvent) -> bool:
    return bool(event.file_path and event.line_number and event.traceback)
