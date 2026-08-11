"""Smoke-test an AWS-deployed ams_monitoring- service.

Default tests are safe: they only call health/dashboard endpoints and invalid-auth
webhook checks. Valid webhook calls are opt-in because they can trigger the real
incident workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://b4g0jr81he.execute-api.us-east-1.amazonaws.com"


@dataclass
class CheckResult:
    name: str
    passed: bool
    status_code: int | None
    detail: str


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any] | str]:
    body = _json_bytes(payload) if payload is not None else None
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)

    request = Request(
        _join_url(base_url, path),
        data=body,
        headers=request_headers,
        method=method.upper(),
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, _parse_body(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, _parse_body(raw)
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def _parse_body(raw: str) -> dict[str, Any] | str:
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]


def _pass_fail(
    name: str,
    status_code: int | None,
    condition: bool,
    detail: str,
) -> CheckResult:
    return CheckResult(name=name, passed=condition, status_code=status_code, detail=detail)


def _contains_sources(body: dict[str, Any] | str) -> bool:
    if not isinstance(body, dict):
        return False
    sources = set(body.get("supported_sources") or [])
    return {"github", "jira", "servicenow"}.issubset(sources)


def check_health(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(base_url, "GET", "/health", timeout=timeout)
    return _pass_fail(
        "GET /health",
        status,
        status == 200 and _contains_sources(body),
        f"status={status}, body={body}",
    )


def check_root(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(base_url, "GET", "/", timeout=timeout)
    return _pass_fail("GET /", status, status == 200, f"status={status}, body={body}")


def check_dashboard_workflow(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(base_url, "GET", "/dashboard/workflow", timeout=timeout)
    ok = status == 200 and isinstance(body, dict) and "nodes" in body and "edges" in body
    return _pass_fail("GET /dashboard/workflow", status, ok, f"status={status}, body={body}")


def check_latest_execution(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(base_url, "GET", "/dashboard/executions/latest", timeout=timeout)
    ok = status == 200 and isinstance(body, dict) and body.get("status") in {"ok", "empty"}
    return _pass_fail(
        "GET /dashboard/executions/latest",
        status,
        ok,
        f"status={status}, body={body}",
    )


def check_unknown_source(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/not-a-source",
        payload={"hello": "world"},
        timeout=timeout,
    )
    return _pass_fail(
        "POST /webhook/incidents/not-a-source",
        status,
        status == 404,
        f"status={status}, body={body}",
    )


def check_jira_invalid_auth(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/jira",
        headers={"X-CodeFixer-Token": "wrong-token"},
        payload={"event": "work_item_created", "issue_key": "SMOKE-INVALID"},
        timeout=timeout,
    )
    return _pass_fail(
        "POST /webhook/incidents/jira invalid token",
        status,
        status == 401,
        f"status={status}, body={body}",
    )


def check_servicenow_invalid_auth(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/servicenow",
        headers={"X-ServiceNow-Token": "wrong-token"},
        payload={"incident": "SMOKE-INVALID", "short_description": "Smoke auth check"},
        timeout=timeout,
    )
    return _pass_fail(
        "POST /webhook/incidents/servicenow invalid token",
        status,
        status == 401,
        f"status={status}, body={body}",
    )


def check_github_invalid_auth(base_url: str, timeout: int) -> CheckResult:
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/github",
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=bad"},
        payload=_github_non_bug_payload(),
        timeout=timeout,
    )
    return _pass_fail(
        "POST /webhook/incidents/github invalid signature",
        status,
        status == 401,
        f"status={status}, body={body}",
    )


def _github_non_bug_payload() -> dict[str, Any]:
    return {
        "action": "opened",
        "issue": {
            "number": 999999,
            "title": "AWS smoke test non-bug issue",
            "body": "This should be ignored by the GitHub normalizer because it has no bug label.",
            "html_url": "https://github.com/example/repo/issues/999999",
            "labels": [{"name": "question"}],
            "user": {"login": "aws-smoke-test"},
            "created_at": "2026-08-10T00:00:00Z",
        },
        "repository": {
            "full_name": "example/repo",
            "clone_url": "https://github.com/example/repo.git",
            "html_url": "https://github.com/example/repo",
            "default_branch": "main",
        },
    }


def send_valid_jira(base_url: str, timeout: int) -> CheckResult:
    token = os.getenv("JIRA_WEBHOOK_TOKEN", "").strip()
    if not token:
        return _pass_fail("valid Jira webhook", None, False, "Set JIRA_WEBHOOK_TOKEN first.")

    payload = {
        "event": "work_item_created",
        "issue_key": f"SMOKE-{int(time.time())}",
        "issue_id": str(int(time.time())),
        "project_key": "SMOKE",
        "issue_type": "Task",
        "summary": "AWS smoke test incident",
        "description": "Smoke test only. No traceback, should not trigger L3 code RCA.",
        "priority": "Low",
        "status": "To Do",
        "labels": "smoke",
        "components": "",
        "reporter": "AWS Smoke Test",
        "created": "2026-08-10T00:00:00.000+0000",
        "url": "https://example.atlassian.net/browse/SMOKE-1",
        "business_application": "Smoke Test Application",
    }
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/jira",
        headers={"X-CodeFixer-Token": token},
        payload=payload,
        timeout=timeout,
    )
    return _pass_fail("valid Jira webhook", status, status == 200, f"status={status}, body={body}")


def send_valid_servicenow(base_url: str, timeout: int) -> CheckResult:
    token = os.getenv("SERVICENOW_WEBHOOK_TOKEN", "").strip()
    if not token:
        return _pass_fail(
            "valid ServiceNow webhook",
            None,
            False,
            "Set SERVICENOW_WEBHOOK_TOKEN first.",
        )

    payload = {
        "incident": f"SMOKE{int(time.time())}",
        "short_description": "AWS smoke test incident",
        "description": "Smoke test only. No traceback, should not trigger L3 code RCA.",
        "priority": "Low",
        "state": "New",
        "caller": "AWS Smoke Test",
        "assignment_group": "Smoke Test",
        "business_application": "Smoke Test Application",
    }
    status, body = _request(
        base_url,
        "POST",
        "/webhook/incidents/servicenow",
        headers={"X-ServiceNow-Token": token},
        payload=payload,
        timeout=timeout,
    )
    return _pass_fail(
        "valid ServiceNow webhook",
        status,
        status == 200,
        f"status={status}, body={body}",
    )


def send_valid_github_non_bug(base_url: str, timeout: int) -> CheckResult:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        return _pass_fail(
            "valid GitHub non-bug webhook",
            None,
            False,
            "Set GITHUB_WEBHOOK_SECRET first.",
        )

    payload = _github_non_bug_payload()
    body = _json_bytes(payload)
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    status, response_body = _request(
        base_url,
        "POST",
        "/webhook/incidents/github",
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
        payload=payload,
        timeout=timeout,
    )
    return _pass_fail(
        "valid GitHub non-bug webhook",
        status,
        status == 200,
        f"status={status}, body={response_body}",
    )


def run_checks(base_url: str, timeout: int, send_valid_webhooks: bool) -> list[CheckResult]:
    checks = [
        check_health,
        check_root,
        check_dashboard_workflow,
        check_latest_execution,
        check_unknown_source,
        check_jira_invalid_auth,
        check_servicenow_invalid_auth,
        check_github_invalid_auth,
    ]
    results = [_run_one_check(check, base_url, timeout) for check in checks]

    if send_valid_webhooks:
        results.extend(
            [
                _run_one_check(send_valid_jira, base_url, timeout),
                _run_one_check(send_valid_servicenow, base_url, timeout),
                _run_one_check(send_valid_github_non_bug, base_url, timeout),
            ]
        )

    return results


def _run_one_check(check, base_url: str, timeout: int) -> CheckResult:
    try:
        return check(base_url, timeout)
    except Exception as exc:
        return CheckResult(
            name=getattr(check, "__name__", "unknown_check"),
            passed=False,
            status_code=None,
            detail=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test ams_monitoring- on AWS.")
    parser.add_argument("--base-url", default=os.getenv("AMS_AWS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--send-valid-webhooks",
        action="store_true",
        help="Send real valid webhook calls using env tokens. These may create workflow executions.",
    )
    args = parser.parse_args()

    print(f"Testing AMS Monitoring AWS endpoint: {args.base_url}")
    results = run_checks(args.base_url, args.timeout, args.send_valid_webhooks)

    failed = 0
    for result in results:
        icon = "PASS" if result.passed else "FAIL"
        status = "-" if result.status_code is None else result.status_code
        print(f"[{icon}] {result.name} status={status}")
        if not result.passed:
            failed += 1
            print(f"       {result.detail}")

    print(f"\nSummary: {len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
