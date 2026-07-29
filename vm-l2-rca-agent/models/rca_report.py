from typing import List
from pydantic import BaseModel


class TargetResource(BaseModel):
    resource_type: str
    application: str


class RCAReport(BaseModel):
    ticket_id: str
    application: str
    technology: str
    problem_domain: str
    recommended_agent: str
    confidence: float
    reason: str
    recommended_checks: List[str]
    target_resource: TargetResource
    status: str