from typing import Optional

from issuelayer.intake.normalizers.github_normaliser import normalise_github_issue
from issuelayer.intake.normalizers.jira_normaliser import normalise_jira_issue
from issuelayer.intake.normalizers.servicenow_normaliser import normalise_servicenow_incident
from issuelayer.intake.schemas import ErrorEvent
from issuelayer.intake.source_event import SourceEvent


def normalise_source_event(source_event: SourceEvent) -> Optional[ErrorEvent]:
    if source_event.source == "github_issue":
        return normalise_github_issue(source_event)
    if source_event.source == "jira":
        return normalise_jira_issue(source_event)
    if source_event.source == "servicenow":
        return normalise_servicenow_incident(source_event)
    raise ValueError(f"Unsupported source event: {source_event.source}")
