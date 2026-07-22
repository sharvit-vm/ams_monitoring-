import os
import re
import uuid
from typing import Optional

from github import Github

from issuelayer.intake.normalizers.common import (
    extract_error_type_message,
    extract_file_info,
    extract_java_class_name,
    extract_traceback,
)
from issuelayer.intake.schemas import ErrorEvent, make_fingerprint
from issuelayer.intake.source_event import SourceEvent


SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
INCIDENT_PATTERNS = {
    "incident_id": re.compile(r"(?:Incident\s*ID|ID)\s*[:\-]\s*(.+)", re.I),
    "priority": re.compile(r"Priority\s*[:\-]\s*(.+)", re.I),
    "configuration_item": re.compile(
        r"(?:Business\s*Application|Application|Configuration\s*Item|CMDB\s*CI|CI)\s*[:\-]\s*(.+)",
        re.I,
    ),
    "incident_status": re.compile(r"Status\s*[:\-]\s*(.+)", re.I),
    "resolution": re.compile(r"Resolution\s*[:\-]\s*(.+)", re.I),
}


def _is_incident_style(body: str) -> bool:
    markers = [
        "incident id",
        "business application",
        "application:",
        "configuration item",
        "cmdb ci",
        "priority:",
        "resolution:",
        "closure code",
        "open date",
    ]
    body_lower = body.lower()
    return sum(1 for marker in markers if marker in body_lower) >= 2


def _extract_incident_fields(text: str) -> dict:
    result = {"description": text[:1000].strip()}
    for field, pattern in INCIDENT_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1).strip().strip("*").strip()
            if value:
                result[field] = value
    return result


def _fetch_comments(repo_full_name: str, issue_number: int) -> list[str]:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return []
    try:
        github = Github(token)
        repo = github.get_repo(repo_full_name)
        issue = repo.get_issue(number=issue_number)
        return [comment.body for comment in issue.get_comments()]
    except Exception as exc:
        print(f"[github] Could not fetch comments: {exc}")
        return []


def normalise_github_issue(source_event: SourceEvent) -> Optional[ErrorEvent]:
    payload = source_event.raw_payload
    action = payload.get("action", "")
    issue = payload.get("issue", {})
    repository = payload.get("repository", {})

    if action not in ("opened", "labeled", "reopened"):
        return None

    repo_url = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url or not repo_full_name:
        return None

    labels = [label.get("name", "").lower() for label in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash", "incident"}
    if not any(label in bug_labels for label in labels):
        return None

    issue_number = issue.get("number", 0)
    issue_title = issue.get("title", "Unknown error")
    issue_body = issue.get("body") or ""
    issue_url = issue.get("html_url", "")
    issue_user = issue.get("user", {}) or {}

    comments = _fetch_comments(repo_full_name, issue_number)
    full_text = issue_body + "\n\n" + "\n\n".join(comments)

    traceback_text = extract_traceback(full_text)
    error_type, message = extract_error_type_message(traceback_text, issue_title)
    file_path, line_number, function_name = extract_file_info(traceback_text)
    class_name = extract_java_class_name(traceback_text)
    is_code_issue = bool(traceback_text or file_path)
    sha_match = SHA_RE.search(issue_body)
    commit_sha = sha_match.group(1) if sha_match else ""

    incident_data = {}
    if _is_incident_style(full_text):
        incident_data = _extract_incident_fields(full_text)
        if not traceback_text:
            error_type = "Incident"
            message = incident_data.get("description", issue_title)[:300]

    # L2 operational context: this must be a business application / service /
    # CMDB CI from the issue body, never the GitHub repository name. Repo fields
    # below are kept separately for L3 code RCA.
    return ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint(error_type, message),
        error_type=error_type,
        message=message,
        traceback=traceback_text,
        file_path=file_path,
        function_name=function_name,
        line_number=line_number,
        class_name=class_name,
        incident_id=incident_data.get("incident_id"),
        description=incident_data.get("description"),
        priority=incident_data.get("priority"),
        configuration_item=incident_data.get("configuration_item"),
        incident_status=incident_data.get("incident_status"),
        resolution=incident_data.get("resolution"),
        short_description=issue_title,
        created_at=issue.get("created_at"),
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        commit_sha=commit_sha,
        branch=repository.get("default_branch", "main"),
        environment="production",
        source="github_issue",
        source_event_id=source_event.id,
        external_id=str(issue_number),
        external_number=issue_number,
        external_url=issue_url,
        raw_description=issue_body,
        labels=labels,
        comments=comments,
        reporter=issue_user.get("login"),
        raw_payload=payload,
        is_code_issue=is_code_issue,
        issue_category="code" if is_code_issue else "incident",
        workflow_action="run_codefix" if is_code_issue else "needs_triage",
        triage_reason=(
            "Traceback or source file was extracted."
            if is_code_issue
            else "No traceback/source file extracted; triage required before code workflow."
        ),
        status="pending",
    )
