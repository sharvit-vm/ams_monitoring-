from typing import List, Optional

from pydantic import BaseModel


class CategorizationResult(BaseModel):

    ticket_id: Optional[str] = None

    classification: str

    support_level: str

    technology: str

    team: str

    priority: str

    business_impact: str

    business_criticality: str

    risk: str

    risk_score: int

    duplicate: bool

    duplicate_of: Optional[str]

    confidence: float

    requires_human: bool

    selected_agent: str

    backup_agent: Optional[str]

    available: bool

    reject: bool

    route_to: str

    reason: str

    reasoning: List[str]

    # AMS graph-routing fields. These let the downstream LangGraph route
    # without translating the legacy response shape.
    is_valid_incident: bool = True

    category: str = "unknown"

    rca_level: str = "human_review"

    needs_human_review: bool = False

    recommended_next_action: str = "human_review"

    source_event_id: Optional[str] = None

    normalised_event_id: Optional[str] = None

