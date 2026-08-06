from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator


_LANGFUSE_CLIENT = None
_LANGFUSE_IMPORT_ERROR = None
_FIELD_LABELS = {
    "event_id": "event",
    "incident_id": "incident",
    "duration_ms": "duration",
    "evidence_ids": "evidence",
    "connected_files": "connected_files",
    "changed_files": "changed_files",
    "files_changed": "files_changed",
    "lines_changed": "lines_changed",
    "source_file_read": "source_file",
    "failing_range_chars": "source_chars",
    "confidence": "confidence",
    "score": "score",
    "calculation": "confidence_calc",
}


def _enabled(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _compact(value: Any, max_chars: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text


def _format_value(value: Any, max_chars: int = 500) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = [_compact(item, 120) for item in value if item not in (None, "")]
        return "[" + ", ".join(items) + "]"
    if isinstance(value, tuple):
        return _format_value(list(value), max_chars=max_chars)
    if isinstance(value, dict):
        pairs = [
            f"{key}:{_compact(item, 120)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        ]
        return "{" + ", ".join(pairs) + "}"
    text = _compact(value, max_chars)
    if any(ch.isspace() for ch in text) or "|" in text:
        return f'"{text}"'
    return text


def _label(key: str) -> str:
    return _FIELD_LABELS.get(key, key)


def log_agent_event(
    *,
    agent: str,
    stage: str,
    status: str,
    event_id: str = "",
    incident_id: str = "",
    summary: str = "",
    **fields: Any,
) -> None:
    """
    Emit a readable, searchable agent log line.

    These logs are intentionally key=value instead of large JSON blobs so an
    operator can follow the flow from Render/CloudWatch quickly.
    """
    parts = [
        f"stage={stage}",
        f"status={status}",
    ]
    if event_id:
        parts.append(f"event={event_id}")
    if incident_id:
        parts.append(f"incident={incident_id}")
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        parts.append(f"{_label(key)}={_format_value(value, 260)}")
    if summary:
        parts.append(f"summary={_format_value(summary, 320)}")
    print(f"[{agent.upper()}] " + " | ".join(parts))


def make_evidence_record(
    *,
    evidence_id: str,
    evidence_type: str,
    tool: str,
    summary: str,
    status: str = "collected",
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    confidence_impact: str = "neutral",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "evidence_id": evidence_id,
        "type": evidence_type,
        "tool": tool,
        "status": status,
        "summary": summary,
        "confidence_impact": confidence_impact,
    }
    if file_path:
        record["file_path"] = file_path
    if line_start:
        record["line_start"] = line_start
    if line_end:
        record["line_end"] = line_end
    if metadata:
        record["metadata"] = metadata
    return record


def _get_langfuse_client():
    global _LANGFUSE_CLIENT, _LANGFUSE_IMPORT_ERROR
    if not _enabled(os.getenv("LANGFUSE_ENABLED"), default=False):
        return None
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT
    if _LANGFUSE_IMPORT_ERROR is not None:
        return None
    try:
        from langfuse import Langfuse

        base_url = (
            os.getenv("LANGFUSE_BASE_URL")
            or os.getenv("LANGFUSE_HOST")
            or "https://cloud.langfuse.com"
        )
        _LANGFUSE_CLIENT = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or None,
            secret_key=os.getenv("LANGFUSE_SECRET_KEY") or None,
            base_url=base_url,
        )
        if hasattr(_LANGFUSE_CLIENT, "auth_check") and not _LANGFUSE_CLIENT.auth_check():
            print(f"[observability] Langfuse auth_check failed; base_url={base_url}")
        else:
            print(f"[observability] Langfuse enabled; base_url={base_url}")
        return _LANGFUSE_CLIENT
    except Exception as exc:  # pragma: no cover - optional dependency/runtime
        _LANGFUSE_IMPORT_ERROR = exc
        print(f"[observability] Langfuse disabled; setup_error={exc}")
        return None


@contextmanager
def trace_span(
    *,
    name: str,
    event_id: str = "",
    incident_id: str = "",
    agent: str = "",
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """
    Optional Langfuse span wrapper.

    Langfuse failures are swallowed because observability must never break RCA
    or remediation. Local logs remain the source of operational visibility.
    """
    client = _get_langfuse_client()
    observation = None
    start = time.perf_counter()
    merged_metadata = {
        "event_id": event_id,
        "incident_id": incident_id,
        "agent": agent,
        **(metadata or {}),
    }
    try:
        if client and hasattr(client, "start_as_current_observation"):
            with client.start_as_current_observation(
                as_type="agent" if agent else "span",
                name=name,
                input=input_data,
                metadata=merged_metadata,
            ) as observation:
                yield observation
                duration_ms = int((time.perf_counter() - start) * 1000)
                if hasattr(observation, "update"):
                    observation.update(
                        output={"status": "completed", "duration_ms": duration_ms},
                        metadata=merged_metadata,
                    )
        else:
            yield observation
    except Exception as exc:  # pragma: no cover - optional dependency/runtime
        print(f"[observability] Langfuse span skipped; name={name} error={exc}")
        yield None
    finally:
        if client and _enabled(os.getenv("LANGFUSE_FLUSH_ON_SPAN"), default=True):
            langfuse_flush()


def langfuse_flush() -> None:
    client = _get_langfuse_client()
    if client and hasattr(client, "flush"):
        try:
            client.flush()
        except Exception as exc:  # pragma: no cover
            print(f"[observability] Langfuse flush failed; error={exc}")
