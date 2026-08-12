import logging
import time
import uuid
import traceback
from contextlib import contextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger("DBFixAgent")

BANNER   = "=" * 80
DIVIDER  = "-" * 80
SERVICE  = "DB-FIX-AGENT"


# ── Context ───────────────────────────────────────────────────────────────────

def bind_context(ticket_id: str, correlation_id: str = None) -> dict:
    return {
        "ticket_id":      ticket_id,
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "timeline":       {}
    }


def _prefix(ctx: dict, step: str = None) -> str:
    parts = [
        f"[svc={SERVICE}]",
        f"[ticket={ctx['ticket_id']}]",
        f"[corr={ctx['correlation_id']}]",
    ]
    if step:
        parts.append(f"[step={step}]")
    return " ".join(parts)


# ── Timeline ──────────────────────────────────────────────────────────────────

def record_timing(ctx: dict, label: str, elapsed: float):
    if "timeline" in ctx:
        ctx["timeline"][label] = elapsed


# ── Banners ───────────────────────────────────────────────────────────────────

def log_start_banner(ctx: dict, ticket_id: str, application: str,
                     technology: str, correlation_id: str, timestamp: str):
    logger.info(BANNER)
    logger.info(f"  {'DB FIX AGENT — AUTONOMOUS REMEDIATION INITIATED':^76}")
    logger.info(BANNER)
    logger.info(f"  Ticket ID      : {ticket_id}")
    logger.info(f"  Application    : {application}")
    logger.info(f"  Technology     : {technology}")
    logger.info(f"  Correlation ID : {correlation_id}")
    logger.info(f"  Timestamp      : {timestamp}")
    logger.info(f"  Service        : {SERVICE}")
    logger.info(BANNER)


def log_final_summary(ctx: dict, ticket_id: str, application: str,
                      database: str, problem_domain: str,
                      initial_status: str, final_status: str,
                      issues_found: int, actions_executed: int,
                      verification: str, total_elapsed: float):
    logger.info(BANNER)
    logger.info(f"  {'✔  MISSION ACCOMPLISHED':^76}")
    logger.info(BANNER)
    logger.info(f"  Ticket          : {ticket_id}")
    logger.info(f"  Application     : {application}")
    logger.info(f"  Database        : {database}")
    logger.info(f"  Problem Domain  : {problem_domain}")
    logger.info(f"  Initial Status  : {initial_status}")
    logger.info(f"  Final Status    : {final_status}")
    logger.info(f"  Issues Found    : {issues_found}")
    logger.info(f"  Actions Executed: {actions_executed}")
    logger.info(f"  Verification    : {verification}")
    logger.info(f"  Execution Time  : {total_elapsed:.3f}s")
    logger.info(DIVIDER)
    logger.info(f"  ➤  Returning Response To L2 RCA Agent")
    logger.info(BANNER)


def log_failure_banner(ctx: dict, error: Exception, total_elapsed: float):
    logger.error(BANNER)
    logger.error(f"  {'✘  AGENT EXECUTION FAILED':^76}")
    logger.error(BANNER)
    logger.error(f"  Ticket         : {ctx['ticket_id']}")
    logger.error(f"  Correlation ID : {ctx['correlation_id']}")
    logger.error(f"  Error Type     : {type(error).__name__}")
    logger.error(f"  Reason         : {error}")
    logger.error(f"  Elapsed        : {total_elapsed:.3f}s")
    logger.error(f"  Retry          : NO — escalating to L2 RCA Agent")
    logger.error(DIVIDER)
    logger.error("  Stack Trace:")
    for line in traceback.format_exc().splitlines():
        logger.error(f"    {line}")
    logger.error(BANNER)


# ── Step Headers ──────────────────────────────────────────────────────────────

def log_step_header(step_num: int, step_name: str, purpose: str):
    logger.info(BANNER)
    logger.info(f"  STEP {step_num:02d}  |  {step_name}")
    logger.info(f"  Purpose : {purpose}")
    logger.info(BANNER)


# ── Generic Step Logging ──────────────────────────────────────────────────────

def log_step_start(ctx: dict, step: str, **details):
    detail_str = "  ".join(f"{k}={v}" for k, v in details.items())
    logger.info(f"{_prefix(ctx, step)} [START]  {detail_str}".rstrip())


def log_step_success(ctx: dict, step: str, elapsed: float, **details):
    detail_str = "  ".join(f"{k}={v}" for k, v in details.items())
    logger.info(
        f"{_prefix(ctx, step)} [SUCCESS]  elapsed={elapsed * 1000:.0f}ms  {detail_str}".rstrip()
    )


def log_step_failure(ctx: dict, step: str, elapsed: float,
                     error: Exception, will_retry: bool = False):
    fate = "RETRYING" if will_retry else "FAILING — no retry scheduled"
    logger.error(
        f"{_prefix(ctx, step)} [FAILURE]  elapsed={elapsed * 1000:.0f}ms  "
        f"fate={fate}  error={type(error).__name__}: {error}"
    )
    logger.error(f"{_prefix(ctx, step)} Stack Trace:")
    for line in traceback.format_exc().splitlines():
        logger.error(f"  {line}")


def log_info(ctx: dict, message: str, step: str = None, **details):
    detail_str = "  ".join(f"{k}={v}" for k, v in details.items())
    logger.info(f"{_prefix(ctx, step)} {message}  {detail_str}".rstrip())


# ── Timed Context Manager ─────────────────────────────────────────────────────

@contextmanager
def timed_step(ctx: dict, step: str, timeline_label: str = None, **start_details):
    log_step_start(ctx, step, **start_details)
    t0 = time.perf_counter()
    try:
        yield
        elapsed = time.perf_counter() - t0
        log_step_success(ctx, step, elapsed)
        if timeline_label:
            record_timing(ctx, timeline_label, elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log_step_failure(ctx, step, elapsed, e, will_retry=False)
        if timeline_label:
            record_timing(ctx, timeline_label, elapsed)
        raise


# ── Execution Timeline ────────────────────────────────────────────────────────

def log_execution_timeline(ctx: dict, total_elapsed: float):
    timeline = ctx.get("timeline", {})
    logger.info(BANNER)
    logger.info(f"  {'EXECUTION TIMELINE':^76}")
    logger.info(BANNER)
    labels = [
        "CMDB Lookup",
        "Relationship Lookup",
        "PostgreSQL Discovery",
        "Database Health Read",
        "Diagnosis",
        "Remediation Rule Lookup",
        "Execution",
        "Verification",
        "Incident History",
        "SN Notification",
    ]
    for label in labels:
        elapsed = timeline.get(label)
        value = f"{elapsed * 1000:.0f}ms" if elapsed is not None else "N/A"
        logger.info(f"  {label:<30} {value:>10}")
    logger.info(DIVIDER)
    logger.info(f"  {'TOTAL EXECUTION TIME':<30} {total_elapsed * 1000:>9.0f}ms")
    logger.info(BANNER)


# ── Specialised Domain Loggers ────────────────────────────────────────────────

def log_servicenow_request(ctx: dict, method: str, table: str, query: str):
    logger.info(f"{_prefix(ctx, 'ServiceNow')} [REQUEST]  "
                f"target=ServiceNow  method={method}  table={table}  query={query}")


def log_servicenow_response(ctx: dict, table: str, status: int,
                             records: int, elapsed: float):
    logger.info(f"{_prefix(ctx, 'ServiceNow')} [RESPONSE]  "
                f"table={table}  status={status}  records={records}  "
                f"elapsed={elapsed * 1000:.0f}ms")


def log_db_lookup(ctx: dict, operation: str, issue_code: str = None):
    detail = f"  issue_code={issue_code}" if issue_code else ""
    logger.info(f"{_prefix(ctx, 'PostgreSQL')} [QUERY]  operation={operation}{detail}")


def log_db_result(ctx: dict, operation: str, result: str, elapsed: float):
    logger.info(f"{_prefix(ctx, 'PostgreSQL')} [RESULT]  "
                f"operation={operation}  result={result}  elapsed={elapsed * 1000:.0f}ms")


def log_health_summary(ctx: dict, label: str, health: dict):
    logger.info(DIVIDER)
    logger.info(f"  {label}")
    logger.info(DIVIDER)
    logger.info(f"  Status      : {health.get('status', 'N/A')}")
    logger.info(f"  Connections : {health.get('active_connections', 'N/A')} / "
                f"{health.get('max_connections', 'N/A')}")
    logger.info(f"  CPU Usage   : {health.get('cpu_usage', 'N/A')}%")
    logger.info(f"  Memory Usage: {health.get('memory_usage', 'N/A')}%")
    logger.info(f"  Slow Queries: {health.get('slow_queries', 'N/A')}")
    logger.info(f"  Deadlocks   : {health.get('deadlocks', 'N/A')}")
    logger.info(DIVIDER)


def log_diagnosis_summary(ctx: dict, issues: list):
    logger.info(DIVIDER)
    logger.info(f"  DIAGNOSIS SUMMARY")
    logger.info(DIVIDER)
    if not issues:
        logger.info("  Issues Found  : NONE — database appears healthy")
    else:
        logger.info(f"  Issues Found  : {len(issues)}")
        for i, issue in enumerate(issues, 1):
            logger.info(f"  [{i}] Issue    : {issue['issue']:<30}  Severity: {issue['severity']}")
    decision = "PROCEED TO REMEDIATION" if issues else "NO REMEDIATION REQUIRED"
    logger.info(f"  Decision      : {decision}")
    logger.info(DIVIDER)


def log_action_execution(ctx: dict, index: int, total: int,
                         action: str, result: str, elapsed: float):
    status_icon = "✔" if result == "SUCCESS" else "✘"
    logger.info(f"{_prefix(ctx, 'Execution')} "
                f"[ACTION {index}/{total}]  {status_icon} {action:<40}  "
                f"result={result}  elapsed={elapsed * 1000:.0f}ms")


def log_verification_result(ctx: dict, health: dict, passed: bool):
    logger.info(DIVIDER)
    logger.info(f"  POST-REMEDIATION VERIFICATION")
    logger.info(DIVIDER)
    logger.info(f"  Status      : {health.get('status', 'N/A')}")
    logger.info(f"  Connections : {health.get('active_connections', 'N/A')}")
    logger.info(f"  CPU Usage   : {health.get('cpu_usage', 'N/A')}%")
    logger.info(f"  Memory Usage: {health.get('memory_usage', 'N/A')}%")
    logger.info(f"  Deadlocks   : {health.get('deadlocks', 'N/A')}")
    logger.info(f"  Slow Queries: {health.get('slow_queries', 'N/A')}")
    logger.info(DIVIDER)
    verdict = "PASSED ✔" if passed else "FAILED ✘"
    logger.info(f"  Verification: {verdict}")
    logger.info(DIVIDER)


def log_incident_save(ctx: dict, action: str, status: str, elapsed: float):
    logger.info(f"{_prefix(ctx, 'IncidentHistory')} [SAVE]  "
                f"action={action}  status={status}  elapsed={elapsed * 1000:.0f}ms")


# ── Legacy aliases (kept for backward compatibility) ──────────────────────────

def log_banner(ctx: dict, title: str):
    logger.info(BANNER)
    logger.info(f"{_prefix(ctx)} {title}")
    logger.info(BANNER)


def log_divider(ctx: dict, title: str):
    logger.info(DIVIDER)
    logger.info(f"{_prefix(ctx)} {title}")
    logger.info(DIVIDER)
