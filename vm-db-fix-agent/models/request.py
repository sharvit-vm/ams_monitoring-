from typing import List

from pydantic import BaseModel


class RCARequest(BaseModel):

    ticket_id: str

    application: str

    technology: str

    problem_domain: str

    recommended_agent: str

    confidence: float

    reason: str

    recommended_checks: List[str]

    status: str