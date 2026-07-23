"""Local adapter around the embedded categorisation agent.

The original categorisation agent remains unchanged in behavior. This module only
turns the normalized ErrorEvent into the payload shape expected by that agent and
normalizes the result shape for this service's workflow.
"""

from __future__ import annotations

from typing import Any

from categorization_layer.agents.categorization_agent import categorize
from issuelayer.intake.schemas import ErrorEvent


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return dict(value)


def categorise_error_event(event: ErrorEvent) -> dict[str, Any]:
    payload = event.model_dump(mode="json")
    result = _to_dict(categorize(payload))

    requires_human = bool(result.get("requires_human", result.get("needs_human_review", False)))
    support_level = str(result.get("support_level") or result.get("rca_level") or "human_review")
    rca_level = str(result.get("rca_level") or support_level or "human_review")

    result.setdefault("is_valid_incident", not bool(result.get("reject", False)))
    result.setdefault("support_level", support_level)
    result.setdefault("rca_level", rca_level)
    result.setdefault("category", result.get("technology") or "unknown")
    result.setdefault("needs_human_review", requires_human)
    result.setdefault("recommended_next_action", result.get("route_to") or "human_review")
    result.setdefault("source_event_id", event.source_event_id)
    result.setdefault("normalised_event_id", event.id)

    return result
