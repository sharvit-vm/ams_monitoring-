from pydantic import BaseModel


class IncidentRequest(BaseModel):

    ticket_id: str

    classification: str

    support_level: str

    technology: str

    team: str

    application: str

    title: str

    description: str

    priority: str

    confidence: float