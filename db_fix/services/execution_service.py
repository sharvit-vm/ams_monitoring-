import time
from db_fix.clients.postgres_client import PostgreSQLClient
from db_fix.utils.logger import log_action_execution


class ExecutionService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def execute(self, actions: list, ctx: dict = None, database_name: str = ""):
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
                    SET active_connections=45, status=
                        CASE WHEN cpu_usage<90 AND memory_usage<90 AND slow_queries<=10 AND deadlocks=0
                        THEN 'HEALTHY' ELSE status END, last_checked=NOW()
                    WHERE app_id=(SELECT app_id FROM applications WHERE LOWER(REPLACE(app_name,' ','_'))=LOWER(REPLACE(%s,' ','_')) LIMIT 1)
                    """,
                    (database_name,),
                    operation="Remediate CONNECTION_POOL_EXHAUSTED",
                    ctx=ctx
                )
                result = "SUCCESS"
            elif issue == "HIGH_CPU":
                self.db.execute(
                    """
                    UPDATE database_health
                    SET cpu_usage=40, status=
                        CASE WHEN active_connections<max_connections AND memory_usage<90 AND slow_queries<=10 AND deadlocks=0
                        THEN 'HEALTHY' ELSE status END, last_checked=NOW()
                    WHERE app_id=(SELECT app_id FROM applications WHERE LOWER(REPLACE(app_name,' ','_'))=LOWER(REPLACE(%s,' ','_')) LIMIT 1)
                    """,
                    (database_name,),
                    operation="Remediate HIGH_CPU",
                    ctx=ctx
                )
                result = "SUCCESS"
            elif issue == "HIGH_MEMORY":
                self.db.execute(
                    """
                    UPDATE database_health
                    SET memory_usage=35, status=
                        CASE WHEN active_connections<max_connections AND cpu_usage<90 AND slow_queries<=10 AND deadlocks=0
                        THEN 'HEALTHY' ELSE status END, last_checked=NOW()
                    WHERE app_id=(SELECT app_id FROM applications WHERE LOWER(REPLACE(app_name,' ','_'))=LOWER(REPLACE(%s,' ','_')) LIMIT 1)
                    """,
                    (database_name,),
                    operation="Remediate HIGH_MEMORY",
                    ctx=ctx
                )
                result = "SUCCESS"
            elif issue == "SLOW_QUERIES":
                self.db.execute(
                    """
                    UPDATE database_health
                    SET slow_queries=0, status=
                        CASE WHEN active_connections<max_connections AND cpu_usage<90 AND memory_usage<90 AND deadlocks=0
                        THEN 'HEALTHY' ELSE status END, last_checked=NOW()
                    WHERE app_id=(SELECT app_id FROM applications WHERE LOWER(REPLACE(app_name,' ','_'))=LOWER(REPLACE(%s,' ','_')) LIMIT 1)
                    """,
                    (database_name,),
                    operation="Remediate SLOW_QUERIES",
                    ctx=ctx
                )
                result = "SUCCESS"
            elif issue == "DEADLOCKS":
                self.db.execute(
                    """
                    UPDATE database_health
                    SET deadlocks=0, status=
                        CASE WHEN active_connections<max_connections AND cpu_usage<90 AND memory_usage<90 AND slow_queries<=10
                        THEN 'HEALTHY' ELSE status END, last_checked=NOW()
                    WHERE app_id=(SELECT app_id FROM applications WHERE LOWER(REPLACE(app_name,' ','_'))=LOWER(REPLACE(%s,' ','_')) LIMIT 1)
                    """,
                    (database_name,),
                    operation="Remediate DEADLOCKS",
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
