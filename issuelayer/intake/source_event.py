from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, Field


class SourceEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    event_type: str
    external_id: str = ""
    received_at: datetime = Field(default_factory=datetime.utcnow)
    headers: dict[str, str] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    raw_body: str = ""
