import time
from models.request import IncidentRequest
from utils.logger import log


class ContextBuilder:

    def build_context(self, incident: IncidentRequest, correlation_id: str = "") -> dict:

        ids = {"ticket_id": incident.ticket_id, "correlation_id": correlation_id}

        log("Context building started", stage="CONTEXT_BUILDING", **ids)
        t0 = time.perf_counter()

        context = {
            "ticket_id": incident.ticket_id,
            "application": incident.application,
            "technology": incident.technology,
            "support_level": incident.support_level,
            "priority": incident.priority,
            "title": incident.title,
            "description": incident.description,
            "team": incident.team,
            "classification": incident.classification,
            "confidence": incident.confidence
        }

        elapsed = (time.perf_counter() - t0) * 1000
        log("Context successfully created", stage="CONTEXT_CREATED",
            elapsed_ms=elapsed,
            extra={"fields_count": len(context)},
            **ids)

        return context
