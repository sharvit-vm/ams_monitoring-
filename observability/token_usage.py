"""Request-local provider usage, shared by SDK and LangChain calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    calls: int = 0
    reported_calls: int = 0
    measurement: str = "unavailable"
    models: list[str] = Field(default_factory=list)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage_dict(value) -> dict:
    return value if isinstance(value, dict) else value.model_dump() if hasattr(value, "model_dump") else {}


class UsageCollector(BaseCallbackHandler):
    """One record per model invocation; safe for ingestion worker threads."""

    def __init__(self):
        self._lock = Lock()
        self._calls = {}

    def start(self, key, model="") -> None:
        with self._lock:
            self._calls.setdefault(str(key), {"model": model, "usage": {}})

    def finish(self, key, usage, model="") -> None:
        with self._lock:
            record = self._calls.setdefault(str(key), {"model": model, "usage": {}})
            record["usage"] = _usage_dict(usage)
            if model:
                record["model"] = model

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        params = kwargs.get("invocation_params") or {}
        self.start(run_id, params.get("model") or params.get("model_name") or "")

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.on_chat_model_start(serialized, prompts, run_id=run_id, **kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        output = response.llm_output or {}
        usage = output.get("token_usage") or output.get("usage") or {}
        model = output.get("model_name") or output.get("model") or ""
        # Chat models expose canonical usage on the generated AIMessage.
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if getattr(message, "usage_metadata", None):
                    usage = message.usage_metadata
                    model = (getattr(message, "response_metadata", {}) or {}).get("model_name") or model
                    break
        self.finish(run_id, usage, model)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.start(run_id)

    def snapshot(self) -> dict:
        with self._lock:
            records = list(self._calls.values())
        counts = []
        for record in records:
            usage = record["usage"]
            inp = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
            out = _count(usage.get("output_tokens", usage.get("completion_tokens")))
            total = _count(usage.get("total_tokens"))
            if total is None and inp is not None and out is not None:
                total = inp + out
            counts.append((inp, out, total))
        reported = sum(row[2] is not None for row in counts)
        def sum_known(index):
            values = [row[index] for row in counts if row[index] is not None]
            return sum(values) if values else None
        measurement = ("not_used" if not records else "provider_reported" if reported == len(records)
                       else "partial" if any(any(value is not None for value in row) for row in counts) else "unavailable")
        return TokenUsage(input_tokens=sum_known(0) if records else 0,
                          output_tokens=sum_known(1) if records else 0,
                          total_tokens=sum_known(2) if records else 0,
                          calls=len(records), reported_calls=reported, measurement=measurement,
                          models=sorted({r["model"] for r in records if r["model"]})).model_dump()


_CURRENT = ContextVar("llm_usage_collector", default=None)


@contextmanager
def usage_scope():
    collector = UsageCollector()
    token = _CURRENT.set(collector)
    try:
        yield collector
    finally:
        _CURRENT.reset(token)


def usage_config() -> dict:
    collector = _CURRENT.get()
    return {"callbacks": [collector]} if collector is not None else {}


def combine_usage(*items) -> dict:
    usages = [_usage_dict(item) for item in items if item]
    if not usages:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0,
                          measurement="not_used").model_dump()
    calls = sum(_count(item.get("calls")) or 0 for item in usages)
    reported_calls = sum(_count(item.get("reported_calls")) or 0 for item in usages)

    def total(field):
        values = [_count(item.get(field)) for item in usages]
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    measurement = ("provider_reported" if calls and reported_calls == calls else
                   "partial" if reported_calls else "not_used" if not calls else "unavailable")
    return TokenUsage(input_tokens=total("input_tokens"), output_tokens=total("output_tokens"),
                      total_tokens=total("total_tokens"), calls=calls,
                      reported_calls=reported_calls, measurement=measurement,
                      models=sorted({model for item in usages for model in item.get("models", [])})).model_dump()


def workflow_usage(response: dict) -> dict:
    """Create the stable dashboard map from existing stage response contracts."""
    categorisation = response.get("categorisation") or {}
    l2 = response.get("l2_rca") or {}
    l2_response = l2.get("response") or l2
    l3 = response.get("l3_rca") or {}
    codefix = response.get("codefix") or {}
    return {
        "categorisation": categorisation.get("token_usage"),
        "l2_rca": l2_response.get("token_usage_details"),
        "l3_context": response.get("l3_context_token_usage"),
        "l3_rca": l3.get("token_usage"),
        "codefix": codefix.get("token_usage"),
    }


def provider_call(create, **kwargs):
    """Capture usage without changing the SDK response or exception contract."""
    collector = _CURRENT.get()
    key = str(uuid4())
    if collector is not None:
        collector.start(key, kwargs.get("model", ""))
    response = create(**kwargs)
    if collector is not None:
        collector.finish(key, getattr(response, "usage", None), getattr(response, "model", ""))
    return response


def track_usage(function=None, *, field="token_usage"):
    """Attach usage to every returned result, including handled failure results."""
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with usage_scope() as collector:
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    exc.token_usage = collector.snapshot()
                    raise
                usage = collector.snapshot()
                if isinstance(result, dict):
                    result[field] = usage
                else:
                    setattr(result, field, usage)
                return result
        return wrapped
    return decorate(function) if function is not None else decorate
