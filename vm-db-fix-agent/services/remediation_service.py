import time
from clients.postgres_client import PostgreSQLClient
from utils.logger import log_info, log_step_failure


class RemediationService:

    def __init__(self):
        self.db = PostgreSQLClient()

    def generate_plan(self, issues: list, ctx: dict = None):
        t0 = time.perf_counter()
        actions = []
        try:
            for issue in issues:
                rule = self.db.fetch_one(
                    "SELECT * FROM remediation_rules WHERE issue_code=%s",
                    (issue["issue"],),
                    operation="Lookup remediation rule",
                    ctx=ctx,
                    issue_code=issue["issue"]
                )
                if rule:
                    actions.append({
                        "issue":     issue["issue"],
                        "action":    rule["action"],
                        "automated": rule["automated"]
                    })
                    if ctx:
                        log_info(ctx, "Remediation rule matched",
                                 step="RemediationService",
                                 issue_code=issue["issue"],
                                 action=rule["action"],
                                 automated=rule["automated"])
                else:
                    if ctx:
                        log_info(ctx, "No remediation rule found — skipping",
                                 step="RemediationService",
                                 issue_code=issue["issue"])
            return actions
        except Exception as e:
            if ctx:
                log_step_failure(ctx, "RemediationService.generate_plan",
                                 time.perf_counter() - t0, e)
            raise
