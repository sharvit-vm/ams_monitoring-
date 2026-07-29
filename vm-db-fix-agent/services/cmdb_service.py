import time
from clients.servicenow_client import ServiceNowClient
from utils.logger import log_step_failure


class CMDBService:

    def __init__(self):
        self.client = ServiceNowClient()

    def get_business_application(self, application_name: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            result = self.client.get(
                table="cmdb_ci_business_app",
                query=f"name={application_name}",
                ctx=ctx
            )
            if not result:
                raise Exception(f"Business Application '{application_name}' not found in CMDB.")
            return result[0]
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "CMDBService.get_business_application",
                                 time.perf_counter() - t0, e)
            raise

    def get_database_relationship(self, parent_sys_id: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            result = self.client.get(
                table="cmdb_rel_ci",
                query=f"parent={parent_sys_id}",
                ctx=ctx
            )
            if not result:
                raise Exception(f"No database relationship found for sys_id={parent_sys_id}.")
            return result[0]
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "CMDBService.get_database_relationship",
                                 time.perf_counter() - t0, e)
            raise

    def get_postgres_instance(self, child_sys_id: str, ctx: dict = None):
        t0 = time.perf_counter()
        try:
            result = self.client.get(
                table="cmdb_ci_db_postgresql_instance",
                query=f"sys_id={child_sys_id}",
                ctx=ctx
            )
            if not result:
                raise Exception(f"PostgreSQL CI not found for sys_id={child_sys_id}.")
            return result[0]
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "CMDBService.get_postgres_instance",
                                 time.perf_counter() - t0, e)
            raise
