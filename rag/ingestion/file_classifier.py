"""Classify repository files into retrieval content and artifact roles."""

from __future__ import annotations

import os
from pathlib import Path

CODE_LANGUAGES = {"python", "javascript", "typescript", "tsx", "go", "java"}
RAG_INCLUDE_TESTS = os.getenv("RAG_INCLUDE_TESTS", "false").strip().lower() in {"1", "true", "yes", "on"}
RAG_INCLUDE_GENERATED_DOCS = os.getenv("RAG_INCLUDE_GENERATED_DOCS", "false").strip().lower() in {"1", "true", "yes", "on"}
DOC_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".json", ".toml", ".env", ".ini", ".properties", ".xml"}
DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "dockerfile",
    "docker-compose.yml",
    "render.yaml",
}
GENERATED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "target",
    "build",
    "dist",
    ".next",
    ".venv",
    "venv",
    "site",
    "apidocs",
    "javadocs",
}
GENERATED_DOC_EXTENSIONS = {".html", ".htm"}
TEST_PARTS = {"test", "tests", "spec", "specs", "fixtures", "fixture", "mock", "mocks"}
DOC_SIGNAL_NAMES = {"readme", "runbook", "playbook", "sop", "operations", "architecture", "troubleshooting"}
INCIDENT_NOTE_TERMS = {"error", "errors", "incident", "incidents", "rca", "postmortem", "known_issue", "known-issue"}


def _parts(file_path: str) -> list[str]:
    return [part.lower() for part in Path(file_path.replace("\\", "/")).parts]


def _stem_terms(path: Path) -> set[str]:
    normalized = path.stem.lower().replace("-", "_")
    return {term for term in normalized.split("_") if term}


def is_generated_path(file_path: str) -> bool:
    path = Path(file_path.replace("\\", "/"))
    parts = set(_parts(file_path))
    if parts & GENERATED_PARTS:
        return True
    if not RAG_INCLUDE_GENERATED_DOCS and path.suffix.lower() in GENERATED_DOC_EXTENSIONS:
        return True
    return False


def is_test_path(file_path: str) -> bool:
    path = Path(file_path.replace("\\", "/"))
    parts = set(_parts(file_path))
    name = path.name.lower()
    return bool(parts & TEST_PARTS or name.startswith("test_") or name.endswith("_test.py") or name.endswith("test.java") or ".spec." in name or ".test." in name)


def is_incident_note_path(file_path: str) -> bool:
    path = Path(file_path.replace("\\", "/"))
    terms = _stem_terms(path)
    return bool(terms & INCIDENT_NOTE_TERMS)


def classify_file(file_path: str, language: str = "") -> str:
    path = Path(file_path.replace("\\", "/"))
    name = path.name.lower()
    suffix = path.suffix.lower()
    language = (language or "").lower()

    if language in CODE_LANGUAGES:
        return "code"
    if name in DEPENDENCY_FILES:
        return "dependency"
    if suffix in DOC_EXTENSIONS:
        return "documentation"
    if suffix in CONFIG_EXTENSIONS:
        return "configuration"
    return "repository_file"


def classify_artifact_type(file_path: str, language: str = "", content_type: str = "") -> str:
    content_type = content_type or classify_file(file_path, language)
    path = Path(file_path.replace("\\", "/"))
    name = path.name.lower()
    stem_terms = _stem_terms(path)

    if is_generated_path(file_path):
        return "generated"
    if content_type == "code" and is_test_path(file_path):
        return "code_test"
    if content_type in {"code", "code_symbol"}:
        return "code_source"
    if content_type == "documentation" and is_incident_note_path(file_path):
        return "incident_note"
    if content_type == "documentation" and (stem_terms & DOC_SIGNAL_NAMES or name.startswith("readme")):
        return "runbook_or_doc"
    if content_type == "documentation":
        return "documentation"
    if content_type == "configuration":
        return "configuration"
    if content_type == "dependency":
        return "dependency"
    return "repository_file"


def artifact_role(artifact_type: str) -> str:
    if artifact_type in {"code_source", "configuration", "dependency"}:
        return "primary"
    if artifact_type in {"documentation", "runbook_or_doc", "incident_note", "code_test"}:
        return "supporting"
    if artifact_type == "generated":
        return "noisy"
    return "supporting"


def source_priority(artifact_type: str, content_type: str = "") -> float:
    if artifact_type == "code_source" and content_type == "code_symbol":
        return 1.0
    if artifact_type == "code_source":
        return 0.9
    if artifact_type == "configuration":
        return 0.8
    if artifact_type == "dependency":
        return 0.7
    if artifact_type == "runbook_or_doc":
        return 0.55
    if artifact_type == "documentation":
        return 0.45
    if artifact_type == "incident_note":
        return 0.3
    if artifact_type == "code_test":
        return 0.3
    if artifact_type == "generated":
        return 0.0
    return 0.4


def retrieval_metadata(file_path: str, language: str = "", content_type: str = "") -> dict[str, object]:
    artifact_type = classify_artifact_type(file_path, language, content_type)
    return {
        "artifact_type": artifact_type,
        "artifact_role": artifact_role(artifact_type),
        "source_priority": source_priority(artifact_type, content_type),
        "is_code_source": artifact_type == "code_source",
        "is_supporting_doc": artifact_type in {"documentation", "runbook_or_doc", "incident_note"},
        "is_incident_note": artifact_type == "incident_note",
        "is_test_file": artifact_type == "code_test",
        "is_generated": artifact_type == "generated",
    }


def is_retrievable_file(file_path: str, language: str = "") -> bool:
    if is_generated_path(file_path):
        return False
    if is_test_path(file_path) and not RAG_INCLUDE_TESTS:
        return False
    return classify_file(file_path, language) in {
        "code",
        "documentation",
        "configuration",
        "dependency",
        "repository_file",
    }