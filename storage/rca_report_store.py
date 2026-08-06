"""
RCA report storage.

Reports are written as JSON artifacts so an incident keeps an auditable RCA
record even after the queue status moves on to code-fix or remediation.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from issuelayer.intake.schemas import ErrorEvent


RCA_REPORT_DIR = os.getenv("RCA_REPORT_DIR", "data/rca_reports")
INCLUDE_RAW_PAYLOAD = os.getenv("RCA_REPORT_INCLUDE_RAW_PAYLOAD", "false").lower() == "true"
RCA_REPORT_SCHEMA_VERSION = "1.0"


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            compacted = _compact(item)
            if compacted in (None, "", [], {}):
                continue
            cleaned[key] = compacted
        return cleaned
    if isinstance(value, list):
        return [item for item in (_compact(item) for item in value) if item not in (None, "", [], {})]
    return value


def _event_dump(event: ErrorEvent) -> dict:
    event_data = event.model_dump(mode="json")
    if not INCLUDE_RAW_PAYLOAD and "raw_payload" in event_data:
        event_data["raw_payload"] = "<omitted from RCA report; set RCA_REPORT_INCLUDE_RAW_PAYLOAD=true to include>"
    return _compact(event_data)


def build_rca_report(event: ErrorEvent, rca_result: Any, knowledge_id: str, repo_dir: str) -> dict:
    return {
        "schema_version": RCA_REPORT_SCHEMA_VERSION,
        "report_id": event.id,
        "created_at": datetime.utcnow().isoformat(),
        "status": "rca_done",
        "source": {
            "system": event.source,
            "source_event_id": event.source_event_id,
            "external_id": event.external_id,
            "external_number": event.external_number,
            "external_url": event.external_url,
            "incident_id": event.incident_id,
        },
        "repository": {
            "repo_url": event.repo_url,
            "repo_full_name": event.repo_full_name,
            "branch": event.branch,
            "commit_sha": event.commit_sha,
            "repo_dir": repo_dir,
            "knowledge_id": knowledge_id,
        },
        "incident": {
            "error_type": event.error_type,
            "message": event.message,
            "priority": event.priority,
            "environment": event.environment,
            "file_path": event.file_path,
            "function_name": event.function_name,
            "line_number": event.line_number,
            "traceback": event.traceback,
        },
        "rca": rca_result.model_dump(mode="json") if hasattr(rca_result, "model_dump") else rca_result,
        "normalised_event": _event_dump(event),
    }


def save_rca_report(
    event: ErrorEvent,
    rca_result: Any,
    knowledge_id: str,
    repo_dir: str,
    report_dir: str | None = None,
) -> str:
    directory = Path(report_dir or RCA_REPORT_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    report = build_rca_report(event, rca_result, knowledge_id, repo_dir)
    report_path = directory / f"{event.id}.json"
    temp_path = directory / f"{event.id}.json.tmp"

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    temp_path.replace(report_path)
    return str(report_path)


def load_rca_report(event_id: str, report_dir: str | None = None) -> dict | None:
    report_path = Path(report_dir or RCA_REPORT_DIR) / f"{event_id}.json"
    if not report_path.exists():
        return None
    with report_path.open("r", encoding="utf-8") as f:
        return json.load(f)
