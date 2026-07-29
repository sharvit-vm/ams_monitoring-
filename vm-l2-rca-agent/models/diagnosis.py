from pydantic import BaseModel
from typing import List, Optional


class AlternativeDiagnosis(BaseModel):
    agent: str
    confidence: float


class Diagnosis(BaseModel):

    problem_domain: str

    recommended_agent: str

    confidence: float

    reason: str

    recommended_checks: List[str]

    evidence: List[str] = []

    alternative_agents: List[AlternativeDiagnosis] = []