import time
from clients.postgres_client import PostgreSQLClient
from utils.logger import log_action_execution


class ExecutionService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def execute(self, actions: list, ctx: dict = None):
        results = []
        total   = len(actions)

        for index, action in enumerate(actions, 1):
            issue  = action["issue"]
            label  = action.get("action", issue)
            t0     = time.perf_counter()

            if issue == "CONNECTION_POOL_EXHAUSTED":
                self.db.execute(
                    """
                    UPDATE database_health
                    SET
                      status='HEALTHY',
                      active_connections=45,
                      cpu_usage=40,
                      memory_usage=35,
                      slow_queries=0,
                      deadlocks=0,
                      last_checked=NOW()
                    WHERE app_id=1
                    """,
                    operation="Apply remediation — reset database health",
                    ctx=ctx
                )
                result = "SUCCESS"
            else:
                result = "SKIPPED"

            elapsed = time.perf_counter() - t0
            if ctx:
                log_action_execution(ctx, index=index, total=total,
                                     action=label, result=result, elapsed=elapsed)

            results.append({"issue": issue, "result": result, "executed": result == "SUCCESS"})

        return results
