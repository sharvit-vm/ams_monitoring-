import time

import requests

from db_fix.config.settings import Settings
from db_fix.utils.logger import logger, log_step_failure


class ServiceNowClient:

    def __init__(self):
        self._settings = Settings()
        self.base_url  = self._settings.SN_INSTANCE.rstrip("/")
        self.auth      = (self._settings.SN_USERNAME, self._settings.SN_PASSWORD)
        self.headers   = {"Accept": "application/json", "Content-Type": "application/json"}

    def _require_config(self) -> None:
        if not self.base_url or not self.auth[0] or not self.auth[1]:
            raise Exception(
                "ServiceNow credentials are not configured. "
                "Set SN_INSTANCE, SN_USERNAME, and SN_PASSWORD in the environment or .env."
            )

    def get(self, table: str, query: str = "", ctx: dict = None) -> list:
        """GET records from a ServiceNow table (used by notification service to look up incident sys_id)."""
        self._require_config()
        url    = f"{self.base_url}/api/now/table/{table}"
        params = {"sysparm_query": query} if query else {}

        t0 = time.perf_counter()
        try:
            response = requests.get(
                url, auth=self.auth, headers=self.headers,
                params=params, timeout=15,
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            result = response.json()["result"]
            logger.info(
                f"[svc=DB-FIX-AGENT] [ServiceNow] [GET]  "
                f"table={table}  status={response.status_code}  "
                f"records={len(result)}  elapsed={elapsed * 1000:.0f}ms"
            )
            return result
        except requests.exceptions.HTTPError as e:
            elapsed = time.perf_counter() - t0
            status_code = e.response.status_code if e.response is not None else None
            msg = (
                "ServiceNow authentication failed. Verify SN_USERNAME and SN_PASSWORD."
                if status_code in {401, 403}
                else f"ServiceNow GET failed with status {status_code} for table={table}"
            )
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except requests.exceptions.ConnectionError as e:
            elapsed = time.perf_counter() - t0
            msg = f"Cannot reach ServiceNow at '{self.base_url}'."
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except requests.exceptions.Timeout as e:
            elapsed = time.perf_counter() - t0
            msg = f"ServiceNow GET timed out after 15s  table={table}"
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except Exception as e:
            elapsed = time.perf_counter() - t0
            if ctx:
                log_step_failure(ctx, f"ServiceNow.GET/{table}", elapsed, e)
            raise

    def patch(self, table: str, sys_id: str, payload: dict, ctx: dict = None) -> dict:
        """PATCH an existing ServiceNow record (used to update incident work notes)."""
        self._require_config()
        url = f"{self.base_url}/api/now/table/{table}/{sys_id}"

        t0 = time.perf_counter()
        try:
            response = requests.patch(
                url, auth=self.auth, headers=self.headers,
                json=payload, timeout=15,
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            result = response.json().get("result", {})
            logger.info(
                f"[svc=DB-FIX-AGENT] [ServiceNow] [PATCH]  "
                f"table={table}  sys_id={sys_id}  status={response.status_code}  "
                f"elapsed={elapsed * 1000:.0f}ms"
            )
            return result
        except requests.exceptions.HTTPError as e:
            elapsed = time.perf_counter() - t0
            status_code = e.response.status_code if e.response is not None else None
            msg = (
                "ServiceNow authentication failed. Verify SN_USERNAME and SN_PASSWORD."
                if status_code in {401, 403}
                else f"ServiceNow PATCH failed with status {status_code} for table={table}"
            )
            if ctx:
                log_step_failure(ctx, f"ServiceNow.PATCH/{table}", elapsed, Exception(msg))
            raise Exception(msg) from e
        except requests.exceptions.ConnectionError as e:
            elapsed = time.perf_counter() - t0
            msg = f"Cannot reach ServiceNow at '{self.base_url}'."
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
