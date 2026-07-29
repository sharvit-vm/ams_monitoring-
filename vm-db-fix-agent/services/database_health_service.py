import time
from clients.postgres_client import PostgreSQLClient
from utils.logger import log_step_failure


class DatabaseHealthService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def get_database_health(self, database_name: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            result = self.db.fetch_one(
                """
                SELECT a.app_name, d.*
                FROM database_health d
                JOIN applications a ON a.app_id = d.app_id
                WHERE LOWER(REPLACE(a.app_name,' ','_')) = %s
                """,
                (database_name.lower(),),
                operation="Read database_health",
                ctx=ctx
            )
            return result
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "DatabaseHealthService.get_database_health",
                                 time.perf_counter() - t0, e)
            raise
