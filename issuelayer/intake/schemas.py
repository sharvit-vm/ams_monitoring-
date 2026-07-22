"""
intake/schemas.py

ErrorEvent is the normalised contract that flows from intake through to
the RCA and code fix agents.

Connectors receive platform-specific events, then normalizers convert
them into this source-agnostic AMS event shape.
"""

import hashlib
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ErrorEvent(BaseModel):
    # Identity
    id: str
    fingerprint: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Technical fields extracted from logs or tracebacks.
    error_type: str
    message: str
    traceback: str = ""
    file_path: str = ""
    function_name: str = ""
    line_number: int = 0
    class_name: str = ""

    # Incident/business fields.
    # configuration_item is operational context for L1/L2 agents, such as a
    # CMDB CI, business application, service, or app display name. It must not
    # be populated from repository identity.
    incident_id: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    configuration_item: Optional[str] = None
    assignment_group: Optional[str] = None
    caller: Optional[str] = None
    opened_by: Optional[str] = None
    incident_status: Optional[str] = None
    resolution: Optional[str] = None
    short_description: Optional[str] = None
    created_at: Optional[str] = None
    opened_at: Optional[str] = None
    resolved_at: Optional[str] = None
    impacted_environment: Optional[str] = None

    # Repository context used by L3 code RCA and code remediation only.
    # L2 operational agents should not treat these values as application/CI.
    repo_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    commit_sha: str = ""
    branch: str = "main"

    # Runtime context.
    environment: str = "production"
    frequency: int = 1
    source: str = "github_issue"

    # Source metadata common to Jira, GitHub, ServiceNow, email, etc.
    source_event_id: Optional[str] = None
    external_id: Optional[str] = None
    external_number: Optional[int] = None
    external_url: Optional[str] = None
    raw_description: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    comments: List[str] = Field(default_factory=list)
    issue_type: Optional[str] = None
    reporter: Optional[str] = None
    project_key: Optional[str] = None
    components: List[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    # Workflow routing.
    is_code_issue: Optional[bool] = None
    issue_category: Optional[str] = None
    workflow_action: Optional[str] = None
    triage_reason: Optional[str] = None

    # Pipeline state.
    status: str = "pending"


def make_fingerprint(error_type: str, message: str) -> str:
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
