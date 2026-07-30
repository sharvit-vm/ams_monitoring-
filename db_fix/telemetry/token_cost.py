"""
telemetry/token_cost.py
────────────────────────────────────────────────────────────────────────────
Token estimation and cost calculation for the DB Fix Agent pipeline.

This agent does not call an LLM directly, but it processes structured
payloads (request, health data, diagnosis, remediation rules, response)
that would be passed to an LLM in a full agentic system.

Token counts are estimated using the standard approximation:
    1 token ≈ 4 characters  (OpenAI / Anthropic rule of thumb)

Cost is calculated using a configurable pricing model so the response
always carries a cost breakdown suitable for enterprise chargeback,
audit, and dashboard display.

Pricing defaults to Amazon Bedrock Claude 3 Sonnet rates (USD per 1K tokens)
but can be overridden via environment variables for any model.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Pricing model ─────────────────────────────────────────────────────────────
# Default: Amazon Bedrock Claude 3 Sonnet (us-east-1) as of 2025
# Override via environment variables for other models / regions.

_INPUT_COST_PER_1K  = float(os.getenv("TOKEN_INPUT_COST_PER_1K",  "0.003"))   # USD
_OUTPUT_COST_PER_1K = float(os.getenv("TOKEN_OUTPUT_COST_PER_1K", "0.015"))   # USD
_MODEL_NAME         = os.getenv("TOKEN_MODEL_NAME", "amazon.nova-pro-v1:0")
_CHARS_PER_TOKEN    = 4  # industry standard approximation


# ── Token estimator ───────────────────────────────────────────────────────────

def _count_tokens(payload: Any) -> int:
    """Estimate token count from any serialisable payload."""
    if payload is None:
        return 0
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, default=str)
        except Exception:
            text = str(payload)
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _compute_cost(tokens: int, cost_per_1k: float) -> float:
    """Compute USD cost for a given token count."""
    return round((tokens / 1000) * cost_per_1k, 6)


# ── Token usage dataclass ─────────────────────────────────────────────────────

@dataclass
class TokenUsage:
    """Tracks token consumption and cost for one pipeline execution."""

    model:              str   = field(default_factory=lambda: _MODEL_NAME)

    # ── Accumulated token counts ──────────────────────────────────────────────
    input_tokens:       int   = 0
    output_tokens:      int   = 0

    # ── Per-stage breakdown ───────────────────────────────────────────────────
    request_tokens:     int   = 0   # inbound RCARequest payload
    health_tokens:      int   = 0   # database_health row read
    diagnosis_tokens:   int   = 0   # issues list produced
    plan_tokens:        int   = 0   # remediation rules fetched
    execution_tokens:   int   = 0   # execution results
    verification_tokens: int  = 0   # post-remediation health read
    response_tokens:    int   = 0   # outbound response payload

    def record_input(self, label: str, payload: Any) -> int:
        """Estimate and accumulate input tokens for a payload."""
        n = _count_tokens(payload)
        self.input_tokens += n
        attr = f"{label}_tokens"
        if hasattr(self, attr):
            setattr(self, attr, getattr(self, attr) + n)
        return n

    def record_output(self, label: str, payload: Any) -> int:
        """Estimate and accumulate output tokens for a payload."""
        n = _count_tokens(payload)
        self.output_tokens += n
        attr = f"{label}_tokens"
        if hasattr(self, attr):
            setattr(self, attr, getattr(self, attr) + n)
        return n

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def input_cost_usd(self) -> float:
        return _compute_cost(self.input_tokens, _INPUT_COST_PER_1K)

    @property
    def output_cost_usd(self) -> float:
        return _compute_cost(self.output_tokens, _OUTPUT_COST_PER_1K)

    @property
    def total_cost_usd(self) -> float:
        return round(self.input_cost_usd + self.output_cost_usd, 6)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "tokens": {
                "input":        self.input_tokens,
                "output":       self.output_tokens,
                "total":        self.total_tokens,
            },
            "token_breakdown": {
                "request":      self.request_tokens,
                "health_read":  self.health_tokens,
                "diagnosis":    self.diagnosis_tokens,
                "plan":         self.plan_tokens,
                "execution":    self.execution_tokens,
                "verification": self.verification_tokens,
                "response":     self.response_tokens,
            },
            "cost_usd": {
                "input":        self.input_cost_usd,
                "output":       self.output_cost_usd,
                "total":        self.total_cost_usd,
            },
            "pricing_model": {
                "input_per_1k_tokens":  _INPUT_COST_PER_1K,
                "output_per_1k_tokens": _OUTPUT_COST_PER_1K,
                "currency":             "USD",
                "estimation_method":    f"1 token ≈ {_CHARS_PER_TOKEN} characters",
            },
        }
