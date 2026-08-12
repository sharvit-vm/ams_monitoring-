import time
from db_fix.clients.postgres_client import PostgreSQLClient
from db_fix.utils.logger import log_step_failure


class DatabaseHealthService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def get_database_health(self, app_id: int, ctx: dict = None) -> dict:
        t0 = time.perf_counter()
        try:
            result = self.db.fetch_one(
                "SELECT * FROM database_health WHERE app_id = %s",
                (app_id,),
                operation="Read database_health",
                ctx=ctx,
            )
            if not result:
                raise ValueError(f"No database_health record found for app_id={app_id}.")
            return dict(result)
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "DatabaseHealthService.get_database_health",
                                 time.perf_counter() - t0, e)
            raise
