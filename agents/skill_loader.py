from __future__ import annotations

import os
from pathlib import Path

from observability.agent_trace import log_agent_event


def _skill_logging_enabled() -> bool:
    return os.getenv("CODEFIXER_LOG_SKILLS", "true").lower() in ("1", "true", "yes")


def _skills_root() -> Path:
    configured = os.getenv("CODEFIXER_SKILLS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "skills"


def load_skill_text(skill_name: str) -> str:
    """
    Load a repo-local skill from skills/<skill_name>/SKILL.md.

    The agents in this repo use LangChain's create_agent directly, so this
    loader gives us skill-style prompt packs without requiring middleware.
    """
    safe_name = (skill_name or "").strip().replace("\\", "/").strip("/")
    if not safe_name or ".." in safe_name.split("/"):
        return ""

    path = _skills_root() / safe_name / "SKILL.md"
    try:
        if not path.is_file():
            if _skill_logging_enabled():
                log_agent_event(
                    agent="skills",
                    stage="skill_lookup",
                    status="missing",
                    skill=safe_name,
                    path=str(path),
                )
            return ""
        text = path.read_text(encoding="utf-8").strip()
        if _skill_logging_enabled():
            log_agent_event(
                agent="skills",
                stage="skill_consumed",
                status="loaded",
                skill=safe_name,
                path=str(path),
                chars=len(text),
                summary="Loaded skill instructions by name only; file content is not printed.",
            )
        return text
    except OSError as exc:
        if _skill_logging_enabled():
            log_agent_event(
                agent="skills",
                stage="skill_lookup",
                status="failed",
                skill=safe_name,
                path=str(path),
                summary=str(exc),
            )
        return ""


def build_skill_prompt(*skill_names: str) -> str:
    blocks = []
    for name in skill_names:
        text = load_skill_text(name)
        if text:
            blocks.append(f"Loaded skill: {name}\n{text}")
    if not blocks:
        return ""
    return "\n\n---\n\n".join(blocks)
