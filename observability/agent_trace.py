from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_LANGFUSE_CLIENT = None
_LANGFUSE_IMPORT_ERROR = None
_WORKFLOW_STEPS: ContextVar[dict[str, Any] | None] = ContextVar(
    "langfuse_workflow_steps", default=None
)
_TRACE_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "langfuse_trace_context", default=None
)
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
    session_id: str = "",
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
    span_manager = None
    attributes_manager = None
    start = time.perf_counter()
    merged_metadata = {
        "event_id": event_id,
        "incident_id": incident_id,
        "agent": agent,
        **(metadata or {}),
    }
    if client and hasattr(client, "start_as_current_observation"):
        try:
            from langfuse import propagate_attributes

            attributes_manager = propagate_attributes(
                session_id=session_id or None,
                metadata=merged_metadata,
            )
            attributes_manager.__enter__()
        except Exception as exc:  # pragma: no cover - optional SDK behavior
            attributes_manager = None
            print(f"[observability] Langfuse session propagation skipped; name={name} error={exc}")
        try:
            span_manager = client.start_as_current_observation(
                as_type="agent" if agent else "span",
                name=name,
                input=input_data,
                metadata=merged_metadata,
            )
            observation = span_manager.__enter__()
        except Exception as exc:  # pragma: no cover - optional dependency/runtime
            span_manager = None
            print(f"[observability] Langfuse span skipped; name={name} error={exc}")

    error_info = (None, None, None)
    try:
        # Yield exactly once so tracing failures cannot mask workflow errors.
        yield observation
    except BaseException:
        error_info = sys.exc_info()
        raise
    finally:
        if observation is not None and error_info[0] is None:
            try:
                if hasattr(observation, "update"):
                    observation.update(
                        output={"status": "completed", "duration_ms": int((time.perf_counter() - start) * 1000)},
                        metadata=merged_metadata,
                    )
            except Exception as exc:  # pragma: no cover - optional dependency/runtime
                print(f"[observability] Langfuse span update failed; name={name} error={exc}")
        if span_manager is not None:
            try:
                span_manager.__exit__(*error_info)
            except Exception as exc:  # pragma: no cover - optional dependency/runtime
                print(f"[observability] Langfuse span close failed; name={name} error={exc}")
        if attributes_manager is not None:
            try:
                attributes_manager.__exit__(*error_info)
            except Exception as exc:  # pragma: no cover - optional dependency/runtime
                print(f"[observability] Langfuse session close failed; name={name} error={exc}")
        if client and _enabled(os.getenv("LANGFUSE_FLUSH_ON_SPAN"), default=True):
            langfuse_flush()


@contextmanager
def generation_span(
    *,
    name: str,
    model: str = "",
    session_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a content-free Langfuse generation observation for token billing."""
    client = _get_langfuse_client()
    observation = None
    merged_metadata = {"session_id": session_id, **(metadata or {})}
    try:
        if client and hasattr(client, "start_as_current_observation"):
            from langfuse import propagate_attributes

            with propagate_attributes(
                session_id=session_id or None,
                metadata=merged_metadata,
            ):
                with client.start_as_current_observation(
                    as_type="generation",
                    name=name,
                    model=model or None,
                    metadata=merged_metadata,
                ) as observation:
                    yield observation
        else:
            yield observation
    except Exception as exc:  # pragma: no cover - optional dependency/runtime
        print(f"[observability] Langfuse generation skipped; name={name} error={exc}")
        yield None
    finally:
        if client and _enabled(os.getenv("LANGFUSE_FLUSH_ON_SPAN"), default=True):
            langfuse_flush()


@contextmanager
def workflow_steps_context() -> Iterator[dict[str, Any]]:
    """Collect child workflow input/output under one root workflow result."""
    steps: dict[str, Any] = {}
    token = _WORKFLOW_STEPS.set(steps)
    try:
        yield steps
    finally:
        _WORKFLOW_STEPS.reset(token)


def current_workflow_steps() -> dict[str, Any] | None:
    return _WORKFLOW_STEPS.get()


def current_trace_context() -> dict[str, str]:
    return dict(_TRACE_CONTEXT.get() or {})


def update_workflow_trace(
    *,
    trace_context: dict[str, str] | None,
    output: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a continuation update to an existing workflow trace.

    Approval-resumed work runs in a later worker job, after the original root
    observation has closed.  Starting a short observation with the persisted
    trace context keeps the continuation in the same trace, while
    ``set_current_trace_io`` updates the root trace output with the latest
    workflow state.  Observability remains best-effort and cannot affect the
    workflow.
    """
    client = _get_langfuse_client()
    trace_context = trace_context or {}
    trace_id = trace_context.get("trace_id")
    if not client or not trace_id or not hasattr(client, "start_as_current_observation"):
        return

    merged_metadata = {
        "workflow_continuation": True,
        **(metadata or {}),
    }
    try:
        with client.start_as_current_observation(
            trace_context={"trace_id": trace_id},
            as_type="span",
            name="workflow.approval_update",
            input={"trace_id": trace_id},
            output=output,
            metadata=merged_metadata,
        ):
            if hasattr(client, "set_current_trace_io"):
                client.set_current_trace_io(output=output)
    except Exception as exc:  # pragma: no cover - optional observability
        print(f"[observability] Workflow trace update skipped; error={exc}")
    finally:
        if _enabled(os.getenv("LANGFUSE_FLUSH_ON_SPAN"), default=True):
            langfuse_flush()


@contextmanager
def workflow_step_span(
    *,
    name: str,
    session_id: str = "",
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
    trace_context: dict[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Record one workflow node with structured input and output.

    The caller writes the final sanitized output to the yielded dictionary.
    Raw incident payloads and source code must be summarized before reaching
    this helper.
    """
    client = _get_langfuse_client()
    result: dict[str, Any] = {}
    started = time.perf_counter()
    merged_metadata = {"session_id": session_id, **(metadata or {})}
    caller_failed = False
    try:
        if client and hasattr(client, "start_as_current_observation"):
            from langfuse import propagate_attributes

            with propagate_attributes(
                session_id=session_id or None,
                metadata=merged_metadata,
            ):
                with client.start_as_current_observation(
                    trace_context=trace_context,
                    as_type="span",
                    name=name,
                    input=input_data,
                    metadata=merged_metadata,
                ) as observation:
                    trace_token = _TRACE_CONTEXT.set({
                        "trace_id": observation.trace_id,
                        "parent_span_id": observation.id,
                    })
                    try:
                        yield result
                    except Exception as exc:
                        caller_failed = True
                        observation.update(
                            output={"status": "failed", "error_type": type(exc).__name__},
                            metadata={
                                **merged_metadata,
                                "duration_ms": int((time.perf_counter() - started) * 1000),
                            },
                        )
                        raise
                    else:
                        observation.update(
                            input=result.get("input", input_data),
                            output=result.get("output", {"status": "completed"}),
                            metadata={
                                **merged_metadata,
                                "duration_ms": int((time.perf_counter() - started) * 1000),
                            },
                        )
                    finally:
                        _TRACE_CONTEXT.reset(trace_token)
        else:
            yield result
    except Exception as exc:  # pragma: no cover - observability is optional
        if caller_failed:
            raise
        print(f"[observability] Workflow span skipped; name={name} error={exc}")
        yield result
    finally:
        if client and _enabled(os.getenv("LANGFUSE_FLUSH_ON_SPAN"), default=True):
            langfuse_flush()


def update_generation_usage(observation: Any, usage: dict[str, Any] | None) -> None:
    """Attach provider-reported token counts without exporting prompt content."""
    if observation is None or not hasattr(observation, "update"):
        return
    usage = usage or {}
    details = {}
    for target, source in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("total", "total_tokens"),
    ):
        value = usage.get(source)
        if isinstance(value, int) and value >= 0:
            details[target] = value
    if details:
        observation.update(usage_details=details)


def langfuse_flush() -> None:
    client = _get_langfuse_client()
    if client and hasattr(client, "flush"):
        try:
            client.flush()
        except Exception as exc:  # pragma: no cover
            print(f"[observability] Langfuse flush failed; error={exc}")
