from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9_]{20,}"),
    re.compile(r"pcsk_[A-Za-z0-9_]{20,}"),
]

_HIGH_RISK_TERMS = {
    "delete database",
    "drop table",
    "truncate table",
    "disable auth",
    "turn off authentication",
    "expose secret",
}

_CHECK_LABELS = {
    "source_supported": "Source Check",
    "payload_secret_scan": "PII Scan",
    "dangerous_request_scan": "Policy Check",
    "l3_repo_context": "L3 Context",
    "l2_operational_context": "L2 Context",
}


@dataclass
class GuardrailDecision:
    allowed: bool
    risk_level: str
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    input_hash: str = ""
    confidence: float = 0.0
    passed_checks: int = 0
    total_checks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "checks": self.checks,
            "input_hash": self.input_hash,
            "confidence": self.confidence,
            "passed_checks": self.passed_checks,
            "total_checks": self.total_checks,
        }


def validate_guardrails(
    *,
    source: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    payload_bytes: bytes,
    event: Any,
) -> GuardrailDecision:
    """Run deterministic safety checks before categorisation and remediation.

    These checks are intentionally conservative and explainable. They do not make
    LLM calls and they do not mutate the event.
    """

    checks: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    source_key = (source or "").lower().strip()
    _record(checks, "source_supported", source_key in {"jira", "servicenow", "github"}, f"source={source_key}")
    if source_key not in {"jira", "servicenow", "github"}:
        blocked_reasons.append(f"Unsupported source '{source}'.")

    raw_text = _safe_json(payload)
    redacted_text = _redact(raw_text)
    has_secret = raw_text != redacted_text
    _record(checks, "payload_secret_scan", not has_secret, "Known token patterns are not allowed in incoming incident text.")
    if has_secret:
        blocked_reasons.append("Incoming payload appears to contain a secret/token.")

    full_text = "\n".join(
        str(value or "")
        for value in [
            getattr(event, "short_description", ""),
            getattr(event, "description", ""),
            getattr(event, "raw_description", ""),
            getattr(event, "message", ""),
            getattr(event, "traceback", ""),
        ]
    ).lower()
    risky_terms = sorted(term for term in _HIGH_RISK_TERMS if term in full_text)
    _record(checks, "dangerous_request_scan", not risky_terms, f"matches={risky_terms}")
    if risky_terms:
        blocked_reasons.append("Incident text requests a dangerous action directly.")

    is_code_issue = bool(getattr(event, "is_code_issue", False))
    repo_url = str(getattr(event, "repo_url", "") or "")
    repo_full_name = str(getattr(event, "repo_full_name", "") or "")
    if is_code_issue and not (repo_url and repo_full_name):
        warnings.append("Code issue does not include repository mapping; L3 route may stop before code RCA.")
        _record(checks, "l3_repo_context", False, "Code issue requires repo_url and repo_full_name for L3 RCA.")
    else:
        _record(checks, "l3_repo_context", True, "Repository context is present or not required.")

    configuration_item = str(getattr(event, "configuration_item", "") or "")
    if not is_code_issue and not configuration_item:
        warnings.append("Operational issue does not include configuration_item/business application.")
        _record(checks, "l2_operational_context", False, "L2 RCA works best with CMDB/business application context.")
    else:
        _record(checks, "l2_operational_context", True, "Operational context is present or not required.")

    passed_checks, total_checks, confidence = _score_checks(checks)

    if blocked_reasons:
        return GuardrailDecision(
            allowed=False,
            risk_level="blocked",
            reason=" ".join(blocked_reasons),
            checks=checks,
            input_hash=_input_hash(payload_bytes, payload),
            confidence=confidence,
            passed_checks=passed_checks,
            total_checks=total_checks,
        )

    risk_level = "medium" if warnings else "low"
    reason = "Passed deterministic guardrails."
    if warnings:
        reason += " " + " ".join(warnings)
    return GuardrailDecision(
        allowed=True,
        risk_level=risk_level,
        reason=reason,
        checks=checks,
        input_hash=_input_hash(payload_bytes, payload),
        confidence=confidence,
        passed_checks=passed_checks,
        total_checks=total_checks,
    )


def _record(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({
        "name": name,
        "display_label": _CHECK_LABELS.get(name, name.replace("_", " ").title()),
        "passed": passed,
        "status": "Passed" if passed else "Failed",
        "detail": detail,
    })


def _score_checks(checks: list[dict[str, Any]]) -> tuple[int, int, float]:
    total = len(checks)
    passed = sum(1 for check in checks if check.get("passed") is not False)
    confidence = round(passed / total, 2) if total else 0.0
    return passed, total, confidence


def _safe_json(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        return str(payload)


def _redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    return redacted


def _input_hash(payload_bytes: bytes, payload: dict[str, Any]) -> str:
    raw = payload_bytes or _safe_json(payload).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


