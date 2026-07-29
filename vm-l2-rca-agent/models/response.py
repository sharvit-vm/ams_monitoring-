from pydantic import BaseModel


class IncidentResponse(BaseModel):

    ticket_id: str

    status: str

    message: str