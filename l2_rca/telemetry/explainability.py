from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel


class AlternativeAgent(BaseModel):
    agent: str
    confidence: float


class Explanation(BaseModel):
    reason: str
    evidence: List[str]
    confidence: float
    decision: str
    execution_summary: str
    timestamp: str
    alternative_agents: List[AlternativeAgent] = []


def build_explanation(
    decision: str,
    reason: str,
    confidence: float,
    recommended_checks: List[str],
    execution_summary: str,
    alternative_agents: Optional[List[dict]] = None
) -> Explanation:

    return Explanation(
        decision=decision,
        reason=reason,
        confidence=confidence,
        evidence=recommended_checks,
        execution_summary=execution_summary,
        timestamp=datetime.now(timezone.utc).isoformat(),
        alternative_agents=[AlternativeAgent(**a) for a in (alternative_agents or [])]
    )
