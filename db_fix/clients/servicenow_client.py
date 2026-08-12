import os
import time

import requests

from db_fix.config.settings import Settings
from db_fix.utils.logger import log_servicenow_request, log_servicenow_response, log_step_failure


class ServiceNowClient:

    def __init__(self):
        self._settings = Settings()
        self.base_url = self._settings.SN_INSTANCE.rstrip("/")
        self.auth = (self._settings.SN_USERNAME, self._settings.SN_PASSWORD)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def _config_error(self, message: str) -> Exception:
        return Exception(message)

    def _require_config(self) -> None:
        if not self.base_url or not self.auth[0] or not self.auth[1]:
            raise self._config_error(
                "ServiceNow credentials are not configured. Set SN_INSTANCE, SN_USERNAME, and SN_PASSWORD in the runtime environment or .env."
            )

    def get(self, table: str, query: str = "", ctx: dict = None):
        self._require_config()
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
        except requests.exceptions.HTTPError as e:
            elapsed = time.perf_counter() - t0
            status_code = e.response.status_code if e.response is not None else None
            if status_code in {401, 403}:
                msg = (
                    "ServiceNow authentication failed. Verify SN_USERNAME and SN_PASSWORD in the runtime environment or .env "
                    f"for instance '{self.base_url}'."
                )
            else:
                msg = f"ServiceNow request failed with status {status_code} for table={table}"
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
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
        self._require_config()
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
        except requests.exceptions.HTTPError as e:
            elapsed = time.perf_counter() - t0
            status_code = e.response.status_code if e.response is not None else None
            if status_code in {401, 403}:
                msg = (
                    "ServiceNow authentication failed. Verify SN_USERNAME and SN_PASSWORD in the runtime environment or .env "
                    f"for instance '{self.base_url}'."
                )
            else:
                msg = f"ServiceNow PATCH failed with status {status_code} for table={table}"
            if ctx:
                log_step_failure(ctx, f"ServiceNow.PATCH/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
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
