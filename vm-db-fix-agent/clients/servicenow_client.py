import time
import requests
from config.settings import settings
from utils.logger import log_servicenow_request, log_servicenow_response, log_step_failure


class ServiceNowClient:

    def __init__(self):
        self.base_url = settings.SN_INSTANCE
        self.auth     = (settings.SN_USERNAME, settings.SN_PASSWORD)
        self.headers  = {"Accept": "application/json", "Content-Type": "application/json"}

    def get(self, table: str, query: str = "", ctx: dict = None):
        url    = f"{self.base_url}/api/now/table/{table}"
        params = {"sysparm_query": query} if query else {}

        if ctx:
            log_servicenow_request(ctx, method="GET", table=table, query=query)

        t0 = time.perf_counter()
        try:
            response = requests.get(
                url, auth=self.auth, headers=self.headers,
                params=params, timeout=15
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            result = response.json()["result"]
            if ctx:
                log_servicenow_response(ctx, table=table,
                                        status=response.status_code,
                                        records=len(result),
                                        elapsed=elapsed)
            return result
        except requests.exceptions.ConnectionError as e:
            elapsed = time.perf_counter() - t0
            msg = (
                f"Cannot reach ServiceNow at '{self.base_url}'. "
                f"If using a Personal Developer Instance, wake it at "
                f"developer.servicenow.com -> My Instance -> Wake."
            )
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except requests.exceptions.Timeout as e:
            elapsed = time.perf_counter() - t0
            msg = f"ServiceNow request timed out after 15s  table={table}"
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except Exception as e:
            elapsed = time.perf_counter() - t0
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, e)
            raise

    def patch(self, table: str, sys_id: str, payload: dict, ctx: dict = None):
        """Update an existing ServiceNow record via PATCH."""
        url = f"{self.base_url}/api/now/table/{table}/{sys_id}"

        if ctx:
            log_servicenow_request(ctx, method="PATCH", table=table,
                                   query=f"sys_id={sys_id}")

        t0 = time.perf_counter()
        try:
            response = requests.patch(
                url, auth=self.auth, headers=self.headers,
                json=payload, timeout=15
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            result = response.json().get("result", {})
            if ctx:
                log_servicenow_response(ctx, table=table,
                                        status=response.status_code,
                                        records=1, elapsed=elapsed)
            return result
        except requests.exceptions.ConnectionError as e:
            elapsed = time.perf_counter() - t0
            msg = (
                f"Cannot reach ServiceNow at '{self.base_url}'. "
                f"If using a Personal Developer Instance, wake it at "
                f"developer.servicenow.com -> My Instance -> Wake."
            )
            if ctx:
                log_step_failure(ctx, f"ServiceNow.PATCH/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except requests.exceptions.Timeout as e:
            elapsed = time.perf_counter() - t0
            msg = f"ServiceNow PATCH timed out after 15s  table={table}  sys_id={sys_id}"
            if ctx:
                log_step_failure(ctx, f"ServiceNow.PATCH/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except Exception as e:
            elapsed = time.perf_counter() - t0
            if ctx:
                log_step_failure(ctx, f"ServiceNow.PATCH/{table}", elapsed, e)
            raise
