"""
clients/postgres_client.py
──────────────────────────────────────────────────────────────────────────────
Production-grade PostgreSQL client for Neon PostgreSQL on Render.

Design decisions
────────────────
• No persistent connection — every operation opens and closes its own
  connection, eliminating "SSL connection has been closed unexpectedly"
  errors caused by Neon's idle-connection timeout.
• Thread-safe by construction — no shared state between requests.
• Full connection lifecycle logged at every stage.
• Existing method signatures (fetch_one / fetch_all / execute) are
  preserved so all upstream services work without modification.
"""

import time
import traceback
from contextlib import contextmanager
from typing import Any, Generator, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from config.settings import settings
from utils.logger import (
    logger,
    log_db_lookup,
    log_db_result,
    log_step_failure,
    DIVIDER,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ctx_prefix(ctx: Optional[dict]) -> str:
    """Return a structured log prefix from the request context, or a safe fallback."""
    if not ctx:
        return "[svc=DB-FIX-AGENT] [ticket=N/A] [corr=N/A]"
    return (
        f"[svc=DB-FIX-AGENT]"
        f" [ticket={ctx.get('ticket_id', 'N/A')}]"
        f" [corr={ctx.get('correlation_id', 'N/A')}]"
    )


def _mask_host(url: str) -> str:
    """Extract and mask the hostname from a DATABASE_URL for safe logging."""
    try:
        # postgresql://user:pass@host/db?...
        host_part = url.split("@")[-1].split("/")[0].split(":")[0]
        if len(host_part) > 8:
            return host_part[:4] + "****" + host_part[-4:]
        return "****"
    except Exception:
        return "<redacted>"


def _extract_db_name(url: str) -> str:
    """Extract the database name from a DATABASE_URL for safe logging."""
    try:
        path = url.split("@")[-1].split("/", 1)[-1].split("?")[0]
        return path or "<unknown>"
    except Exception:
        return "<unknown>"


# ── Core connection factory ───────────────────────────────────────────────────

@contextmanager
def _managed_connection(
    operation: str,
    ctx: Optional[dict],
    write: bool = False,
) -> Generator[psycopg2.extensions.cursor, None, None]:
    """
    Context manager that owns the full PostgreSQL connection lifecycle:

        Open connection → open cursor → yield cursor
        → commit (write ops) / rollback (on error)
        → close cursor → close connection

    Parameters
    ----------
    operation : str
        Human-readable name of the database operation (used in logs).
    ctx : dict | None
        Request context carrying ticket_id and correlation_id.
    write : bool
        When True, commits on success and rolls back on failure.
        When False (read), no commit is issued.
    """
    prefix     = _ctx_prefix(ctx)
    masked_host = _mask_host(settings.DATABASE_URL)
    db_name    = _extract_db_name(settings.DATABASE_URL)
    conn       = None
    cursor     = None
    t_conn     = time.perf_counter()

    # ── Open connection ───────────────────────────────────────────────────────
    logger.info(
        f"{prefix} [PostgreSQL] [CONNECTING]  "
        f"operation={operation}  host={masked_host}  "
        f"db={db_name}  ssl=enabled"
    )
    try:
        conn = psycopg2.connect(settings.DATABASE_URL, cursor_factory=RealDictCursor)
        conn_elapsed = (time.perf_counter() - t_conn) * 1000
        logger.info(
            f"{prefix} [PostgreSQL] [CONNECTED]  "
            f"operation={operation}  host={masked_host}  "
            f"db={db_name}  elapsed={conn_elapsed:.0f}ms"
        )
    except Exception as exc:
        conn_elapsed = (time.perf_counter() - t_conn) * 1000
        logger.error(
            f"{prefix} [PostgreSQL] [CONNECTION FAILED]  "
            f"operation={operation}  host={masked_host}  "
            f"elapsed={conn_elapsed:.0f}ms  "
            f"error={type(exc).__name__}: {exc}"
        )
        logger.error(f"{prefix} [PostgreSQL] Stack Trace:")
        for line in traceback.format_exc().splitlines():
            logger.error(f"  {line}")
        raise

    # ── Open cursor ───────────────────────────────────────────────────────────
    try:
        cursor = conn.cursor()
        logger.info(
            f"{prefix} [PostgreSQL] [CURSOR OPENED]  operation={operation}"
        )

        # ── Yield cursor to caller ────────────────────────────────────────────
        t_query = time.perf_counter()
        logger.info(
            f"{prefix} [PostgreSQL] [EXECUTING]  operation={operation}"
        )
        yield cursor
        query_elapsed = (time.perf_counter() - t_query) * 1000

        logger.info(
            f"{prefix} [PostgreSQL] [EXECUTED]  "
            f"operation={operation}  elapsed={query_elapsed:.0f}ms"
        )

        # ── Commit (write operations only) ────────────────────────────────────
        if write:
            conn.commit()
            logger.info(
                f"{prefix} [PostgreSQL] [COMMITTED]  operation={operation}"
            )

    except Exception as exc:
        query_elapsed = (time.perf_counter() - t_query) * 1000

        # ── Rollback ──────────────────────────────────────────────────────────
        rollback_status = "N/A"
        if write and conn:
            try:
                conn.rollback()
                rollback_status = "ROLLED BACK"
                logger.warning(
                    f"{prefix} [PostgreSQL] [ROLLBACK]  operation={operation}"
                )
            except Exception as rb_exc:
                rollback_status = f"ROLLBACK FAILED: {rb_exc}"
                logger.error(
                    f"{prefix} [PostgreSQL] [ROLLBACK FAILED]  "
                    f"operation={operation}  error={rb_exc}"
                )

        logger.error(
            f"{prefix} [PostgreSQL] [QUERY FAILED]  "
            f"operation={operation}  elapsed={query_elapsed:.0f}ms  "
            f"rollback={rollback_status}  "
            f"error={type(exc).__name__}: {exc}"
        )
        logger.error(f"{prefix} [PostgreSQL] Stack Trace:")
        for line in traceback.format_exc().splitlines():
            logger.error(f"  {line}")
        raise

    finally:
        # ── Close cursor ──────────────────────────────────────────────────────
        if cursor is not None:
            try:
                cursor.close()
                logger.info(
                    f"{prefix} [PostgreSQL] [CURSOR CLOSED]  operation={operation}"
                )
            except Exception:
                pass

        # ── Close connection ──────────────────────────────────────────────────
        if conn is not None:
            try:
                conn.close()
                total_elapsed = (time.perf_counter() - t_conn) * 1000
                logger.info(
                    f"{prefix} [PostgreSQL] [CONNECTION CLOSED]  "
                    f"operation={operation}  total_elapsed={total_elapsed:.0f}ms"
                )
            except Exception:
                pass


# ── Public client ─────────────────────────────────────────────────────────────

class PostgreSQLClient:
    """
    Stateless PostgreSQL client.

    Every method opens a fresh connection, executes the query, and closes
    the connection — regardless of success or failure.  There is no shared
    state, making this class fully thread-safe and safe for use on Render
    with Neon PostgreSQL.
    """

    # No __init__ connection — intentionally stateless.

    # ── fetch_one ─────────────────────────────────────────────────────────────

    def fetch_one(
        self,
        query: str,
        params: Any = None,
        operation: str = "fetch_one",
        ctx: Optional[dict] = None,
        issue_code: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Execute a SELECT and return the first row as a dict, or None.

        Signature is backward-compatible with all existing service callers.
        """
        active_ctx = ctx
        if active_ctx:
            log_db_lookup(active_ctx, operation=operation, issue_code=issue_code)

        t0 = time.perf_counter()
        try:
            with _managed_connection(operation, active_ctx, write=False) as cursor:
                cursor.execute(query, params)
                result: Optional[dict] = cursor.fetchone()

            elapsed = time.perf_counter() - t0
            row_count = 1 if result else 0

            logger.info(
                f"{_ctx_prefix(active_ctx)} [PostgreSQL] [ROWS RETURNED]  "
                f"operation={operation}  rows={row_count}"
            )
            if active_ctx:
                log_db_result(
                    active_ctx,
                    operation=operation,
                    result="FOUND" if result else "NOT FOUND",
                    elapsed=elapsed,
                )
            return result

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            if active_ctx:
                log_step_failure(active_ctx, f"PostgreSQL/{operation}", elapsed, exc)
            raise

    # ── fetch_all ─────────────────────────────────────────────────────────────

    def fetch_all(
        self,
        query: str,
        params: Any = None,
        operation: str = "fetch_all",
        ctx: Optional[dict] = None,
    ) -> list:
        """
        Execute a SELECT and return all rows as a list of dicts.

        Signature is backward-compatible with all existing service callers.
        """
        active_ctx = ctx
        if active_ctx:
            log_db_lookup(active_ctx, operation=operation)

        t0 = time.perf_counter()
        try:
            with _managed_connection(operation, active_ctx, write=False) as cursor:
                cursor.execute(query, params)
                result: list = cursor.fetchall()

            elapsed = time.perf_counter() - t0

            logger.info(
                f"{_ctx_prefix(active_ctx)} [PostgreSQL] [ROWS RETURNED]  "
                f"operation={operation}  rows={len(result)}"
            )
            if active_ctx:
                log_db_result(
                    active_ctx,
                    operation=operation,
                    result=f"{len(result)} row(s)",
                    elapsed=elapsed,
                )
            return result

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            if active_ctx:
                log_step_failure(active_ctx, f"PostgreSQL/{operation}", elapsed, exc)
            raise

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        query: str,
        params: Any = None,
        operation: str = "execute",
        ctx: Optional[dict] = None,
    ) -> None:
        """
        Execute an INSERT / UPDATE / DELETE and commit.

        Signature is backward-compatible with all existing service callers.
        """
        active_ctx = ctx
        if active_ctx:
            log_db_lookup(active_ctx, operation=operation)

        t0 = time.perf_counter()
        try:
            with _managed_connection(operation, active_ctx, write=True) as cursor:
                cursor.execute(query, params)

            elapsed = time.perf_counter() - t0
            if active_ctx:
                log_db_result(
                    active_ctx,
                    operation=operation,
                    result="COMMITTED",
                    elapsed=elapsed,
                )

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            if active_ctx:
                log_step_failure(active_ctx, f"PostgreSQL/{operation}", elapsed, exc)
            raise
