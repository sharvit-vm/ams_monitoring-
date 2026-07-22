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
LABELED_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z _-]{1,60})\s*:\s*(.+?)\s*$", re.MULTILINE)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_as_text(item) for item in value if item is not None)
    return str(value)


def _extract_labeled_value(text: str, *labels: str) -> str:
    expected = {label.strip().lower().replace("_", " ") for label in labels}
    for match in LABELED_FIELD_RE.finditer(text or ""):
        label = match.group(1).strip().lower().replace("_", " ")
        value = match.group(2).strip()
        if label in expected and value:
            return value
    return ""


def _business_application_from_payload(payload: dict, description: str) -> str:
    explicit = (
        payload.get("business_application")
        or payload.get("Business Application")
        or payload.get("application")
        or payload.get("Application")
        or payload.get("configuration_item")
        or payload.get("configurationItem")
        or payload.get("cmdb_ci")
        or payload.get("cmdbCi")
    )
    if explicit:
        return _as_text(explicit)
    return _extract_labeled_value(
        description,
        "business application",
        "application",
        "configuration item",
        "cmdb ci",
        "ci",
    )


def _repo_from_payload_or_env(payload: dict) -> tuple[str, str, str]:
    repo_url = (
        payload.get("repo_url")
        or os.getenv("JIRA_DEFAULT_REPO_URL")
        or os.getenv("DEFAULT_REPO_URL")
        or ""
    )
    repo_full_name = (
        payload.get("repo_full_name")
        or os.getenv("JIRA_DEFAULT_REPO_FULL_NAME")
        or os.getenv("DEFAULT_REPO_FULL_NAME")
        or ""
    )
    branch = payload.get("branch") or os.getenv("JIRA_DEFAULT_BRANCH", "main")

    text = "\n".join([
        _as_text(payload.get("description")),
        _as_text(payload.get("summary")),
        _as_text(payload.get("url")),
    ])
    match = GITHUB_RE.search(text)
    if match:
        repo_full_name = repo_full_name or match.group(1)
        repo_url = repo_url or f"https://github.com/{match.group(1)}.git"

    return repo_url, repo_full_name, branch


def normalise_jira_issue(source_event: SourceEvent) -> Optional[ErrorEvent]:
    payload = source_event.raw_payload
    issue_key = _as_text(payload.get("issue_key") or payload.get("key") or source_event.external_id)
    summary = _as_text(payload.get("summary") or "Jira work item")
    description = _as_text(payload.get("description"))
    priority = _as_text(payload.get("priority"))
    status = _as_text(payload.get("status"))
    project_key = _as_text(payload.get("project_key"))
    components = _as_text(payload.get("components"))
    labels = _as_text(payload.get("labels"))
    url = _as_text(payload.get("url"))
    issue_type = _as_text(payload.get("issue_type"))
    reporter = _as_text(payload.get("reporter"))
    created = _as_text(payload.get("created"))
    business_application = _business_application_from_payload(payload, description)

    repo_url, repo_full_name, branch = _repo_from_payload_or_env(payload)

    full_text = "\n\n".join(
        part for part in [
            summary,
            description,
            f"Labels: {labels}" if labels else "",
            f"Components: {components}" if components else "",
        ]
        if part
    )

    traceback_text = extract_traceback(full_text)
    error_type, message = extract_error_type_message(traceback_text, summary)
    file_path, line_number, function_name = extract_file_info(traceback_text)
    is_code_issue = bool(traceback_text or file_path)
    fingerprint_seed = f"{issue_key}::{error_type}::{message}"

    # L2 operational context: Jira must provide a business app / CMDB CI via
    # explicit webhook fields, a labeled field, or Components. Do not use the
    # Jira project key or repository identity as an L2 application.
    configuration_item = business_application or components or None

    return ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint("jira", fingerprint_seed),
        error_type=error_type,
        message=message,
        traceback=traceback_text,
        file_path=file_path,
        function_name=function_name,
        line_number=line_number,
        incident_id=issue_key or None,
        description=description or summary,
        priority=priority or None,
        configuration_item=configuration_item,
        incident_status=status or None,
        short_description=summary or None,
        created_at=created or None,
        # L3 code context: repo fields are only for code RCA / code fix.
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        branch=branch,
        environment="production",
        source="jira",
        source_event_id=source_event.id,
        external_id=issue_key or None,
        external_url=url or None,
        raw_description=description or summary,
        labels=[label.strip() for label in labels.split(",") if label.strip()],
        issue_type=issue_type or None,
        reporter=reporter or None,
        project_key=project_key or None,
        components=[component.strip() for component in components.split(",") if component.strip()],
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
