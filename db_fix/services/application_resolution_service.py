"""
services/application_resolution_service.py
────────────────────────────────────────────────────────────────────────────
Resolves an application by name from the Enterprise Operations Database
(applications table) and returns the app_id along with all associated
operational data needed by the pipeline.

Replaces the 3-step CMDB flow (Business Application → Relationship →
PostgreSQL Instance) with a single direct database lookup.
"""

import time
from db_fix.clients.postgres_client import PostgreSQLClient
from db_fix.utils.logger import log_info, log_step_failure


class ApplicationResolutionService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def resolve(self, application_name: str, ctx: dict = None) -> dict:
        """
        Resolve the application record by name.

        Returns a dict with app_id, app_name, and all columns from the
        applications table. Raises if the application is not found.
        """
        t0 = time.perf_counter()
        try:
            record = self.db.fetch_one(
                "SELECT * FROM applications WHERE LOWER(app_name) = LOWER(%s)",
                (application_name,),
                operation="Resolve application by name",
                ctx=ctx,
            )
            if not record:
                raise ValueError(
                    f"Application '{application_name}' not found in the applications table."
                )
            elapsed = time.perf_counter() - t0
            if ctx:
                log_info(
                    ctx,
                    "Application resolved",
                    step="APPLICATION_RESOLUTION",
                    app_id=record["app_id"],
                    app_name=record["app_name"],
                    elapsed_ms=f"{elapsed * 1000:.0f}ms",
                )
            return dict(record)
        except Exception as e:
            if ctx:
                log_step_failure(
                    ctx, "ApplicationResolutionService.resolve",
                    time.perf_counter() - t0, e,
                )
            raise
