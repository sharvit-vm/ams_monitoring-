import time
from clients.postgres_client import PostgreSQLClient
from utils.logger import log_incident_save, log_step_failure


class IncidentHistoryService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def save(self, ticket_id: str, application: str,
             action: str, status: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            self.db.execute(
                """
                INSERT INTO incident_history (ticket_id, application, action_taken, status)
                VALUES (%s, %s, %s, %s)
                """,
                (ticket_id, application, action, status),
                operation="Insert incident history record",
                ctx=ctx
            )
            elapsed = time.perf_counter() - t0
            if ctx:
                log_incident_save(ctx, action=action, status=status, elapsed=elapsed)
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "IncidentHistoryService.save",
                                 time.perf_counter() - t0, e)
            raise
