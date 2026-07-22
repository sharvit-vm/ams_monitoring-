import os
import re
import uuid
from typing import Optional

from issuelayer.intake.normalizers.common import (
    extract_error_type_message,
    extract_file_info,
    extract_traceback,
)
from issuelayer.intake.schemas import ErrorEvent, make_fingerprint
from issuelayer.intake.source_event import SourceEvent


GITHUB_RE = re.compile(r"https://github\.com/([^/\s]+/[^/\s.]+)(?:\.git)?")


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("display_value", "value", "name", "number"):
            if value.get(key):
                return _as_text(value.get(key))
        return ", ".join(f"{k}={_as_text(v)}" for k, v in value.items() if v is not None)
    if isinstance(value, list):
        return ", ".join(_as_text(item) for item in value if item is not None)
    return str(value)


def _first_text(*values) -> str:
    for value in values:
        text = _as_text(value).strip()
        if text:
            return text
    return ""


def _repo_from_payload_or_env(payload: dict) -> tuple[str, str, str]:
    repo_url = (
        payload.get("repo_url")
        or os.getenv("SERVICENOW_DEFAULT_REPO_URL")
        or os.getenv("DEFAULT_REPO_URL")
        or ""
    )
    repo_full_name = (
        payload.get("repo_full_name")
        or os.getenv("SERVICENOW_DEFAULT_REPO_FULL_NAME")
        or os.getenv("DEFAULT_REPO_FULL_NAME")
        or ""
    )
    branch = payload.get("branch") or os.getenv("SERVICENOW_DEFAULT_BRANCH", "main")

    text = "\n".join(
        [
            _as_text(payload.get("description")),
            _as_text(payload.get("short_description")),
            _as_text(payload.get("comments")),
            _as_text(payload.get("work_notes")),
            _as_text(payload.get("url")),
        ]
    )
    match = GITHUB_RE.search(text)
    if match:
        repo_full_name = repo_full_name or match.group(1)
        repo_url = repo_url or f"https://github.com/{match.group(1)}.git"

    return _as_text(repo_url), _as_text(repo_full_name), _as_text(branch) or "main"


def _priority_from_payload(payload: dict) -> str:
    priority = _first_text(payload.get("priority"), payload.get("severity"))
    if priority:
        return priority

    impact = _first_text(payload.get("impact"))
    urgency = _first_text(payload.get("urgency"))
    if impact or urgency:
        return " / ".join(part for part in [f"impact={impact}" if impact else "", f"urgency={urgency}" if urgency else ""] if part)
    return ""


def normalise_servicenow_incident(source_event: SourceEvent) -> Optional[ErrorEvent]:
    payload = source_event.raw_payload
    incident_number = _first_text(
        payload.get("number"),
        payload.get("incident"),
        payload.get("incident_id"),
        source_event.external_id,
    )
    sys_id = _first_text(payload.get("sys_id"))
    summary = _first_text(
        payload.get("short_description"),
        payload.get("summary"),
        payload.get("title"),
        "ServiceNow incident",
    )
    description = _first_text(payload.get("description"))
    comments = _first_text(payload.get("comments"), payload.get("work_notes"), payload.get("close_notes"))
    state = _first_text(payload.get("state"), payload.get("incident_state"), payload.get("status"))
    priority = _priority_from_payload(payload)
    assignment_group = _first_text(payload.get("assignment_group"))
    caller = _first_text(payload.get("caller"), payload.get("caller_id"))
    opened_by = _first_text(payload.get("opened_by"), payload.get("opened by"))
    created_at = _first_text(payload.get("created"), payload.get("created_at"), payload.get("sys_created_on"))
    opened_at = _first_text(payload.get("opened"), payload.get("opened_at"), payload.get("opened_at_time"))
    resolved_at = _first_text(payload.get("resolved"), payload.get("resolved_at"), payload.get("resolved_at_time"))
    impacted_environment = _first_text(
        payload.get("impacted_environment"),
        payload.get("impacted environment"),
        payload.get("environment"),
        payload.get("env"),
    )
    # L2 operational context: use CMDB/business-service fields only. Assignment
    # group is ownership/team context, not an application or CI, and repository
    # identity is kept separately for L3 code RCA.
    configuration_item = _first_text(
        payload.get("business_application"),
        payload.get("business application"),
        payload.get("business_app"),
        payload.get("business app"),
        payload.get("cmdb_ci"),
        payload.get("cmdb ci"),
        payload.get("configuration_item"),
        payload.get("configuration item"),
        payload.get("business_service"),
        payload.get("business service"),
        payload.get("service"),
    )
    url = _first_text(payload.get("url"), payload.get("record_url"), payload.get("incident_url"))

    repo_url, repo_full_name, branch = _repo_from_payload_or_env(payload)

    full_text = "\n\n".join(
        part
        for part in [
            summary,
            description,
            comments,
            f"CI: {configuration_item}" if configuration_item else "",
            f"Priority: {priority}" if priority else "",
        ]
        if part
    )

    traceback_text = extract_traceback(full_text)
    error_type, message = extract_error_type_message(traceback_text, summary)
    file_path, line_number, function_name = extract_file_info(traceback_text)
    is_code_issue = bool(traceback_text or file_path)
    fingerprint_seed = f"{incident_number or sys_id}::{error_type}::{message}"

    return ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint("servicenow", fingerprint_seed),
        error_type=error_type,
        message=message,
        traceback=traceback_text,
        file_path=file_path,
        function_name=function_name,
        line_number=line_number,
        incident_id=incident_number or sys_id or None,
        description=description or summary,
        priority=priority or None,
        configuration_item=configuration_item or None,
        assignment_group=assignment_group or None,
        caller=caller or None,
        opened_by=opened_by or None,
        incident_status=state or None,
        resolution=_first_text(payload.get("resolution"), payload.get("close_notes")) or None,
        short_description=summary or None,
        created_at=created_at or None,
        opened_at=opened_at or None,
        resolved_at=resolved_at or None,
        impacted_environment=impacted_environment or None,
        # L3 code context: repo fields are only for code RCA / code fix.
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        branch=branch,
        environment=impacted_environment or "production",
        source="servicenow",
        source_event_id=source_event.id,
        external_id=incident_number or sys_id or None,
        external_url=url or None,
        raw_description=description or summary,
        labels=[
            label
            for label in [
                _first_text(payload.get("category")),
                _first_text(payload.get("subcategory")),
                _first_text(payload.get("assignment_group")),
            ]
            if label
        ],
        comments=[comments] if comments else [],
        raw_payload=payload,
        is_code_issue=is_code_issue,
        issue_category="code" if is_code_issue else "unknown",
        workflow_action="run_codefix" if is_code_issue else "needs_triage",
        triage_reason=(
            "Traceback or source file was extracted."
            if is_code_issue
            else "No traceback/source file extracted; triage required before code workflow."
        ),
        status="pending",
    )
