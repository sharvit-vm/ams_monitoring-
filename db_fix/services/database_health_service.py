import time
from db_fix.clients.postgres_client import PostgreSQLClient
from db_fix.utils.logger import log_step_failure


class DatabaseHealthService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def get_database_health(self, database_name: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            normalized = database_name.lower().replace(" ", "_")
            result = self.db.fetch_one(
                """
                SELECT a.app_name, d.*
                FROM database_health d
                JOIN applications a ON a.app_id = d.app_id
                WHERE LOWER(REPLACE(a.app_name,' ','_')) = %s
                """,
                (normalized,),
                operation="Read database_health",
                ctx=ctx
            )
            if result is None:
                raise ValueError(
                    f"No database_health record found for application '{database_name}'. "
                    f"Ensure an entry exists in the applications + database_health tables."
                )
            return result
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "DatabaseHealthService.get_database_health",
                                 time.perf_counter() - t0, e)
            raise
