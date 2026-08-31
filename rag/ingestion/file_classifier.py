"""Classify repository files into retrieval content types."""

from __future__ import annotations

from pathlib import Path

CODE_LANGUAGES = {"python", "javascript", "typescript", "tsx", "go", "java"}
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


def classify_file(file_path: str, language: str = "") -> str:
    path = Path(file_path)
    name = path.name.lower()
    suffix = path.suffix.lower()

    if language in CODE_LANGUAGES:
        return "code"
    if name in DEPENDENCY_FILES:
        return "dependency"
    if suffix in DOC_EXTENSIONS:
        return "documentation"
    if suffix in CONFIG_EXTENSIONS:
        return "configuration"
    return "repository_file"


def is_retrievable_file(file_path: str, language: str = "") -> bool:
    return classify_file(file_path, language) in {
        "code",
        "documentation",
        "configuration",
        "dependency",
        "repository_file",
    }
