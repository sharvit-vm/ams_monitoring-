"""
workflows/agent_registry.py
────────────────────────────────────────────────────────────────────────────
Central registry mapping problem_domain → in-process fix agent callable.

To add a new agent (e.g. config_fix_agent):
  1. Implement the agent under a new namespace folder e.g. config_fix/
  2. Add one entry to AGENT_REGISTRY below.
  The LangGraph workflow picks it up automatically.
"""

from __future__ import annotations
from typing import Callable


def _db_fix_agent_factory() -> Callable:
    from db_fix.agents.db_fix_agent import DBFixAgent
    agent = DBFixAgent()
    return agent.execute


AGENT_REGISTRY: dict[str, Callable[[], Callable]] = {
    "database": _db_fix_agent_factory,
    # "configuration":  _config_fix_agent_factory,   ← future
    # "middleware":     _middleware_fix_agent_factory, ← future
}


def get_fix_agent(problem_domain: str) -> Callable | None:
    factory = AGENT_REGISTRY.get(problem_domain.strip().lower())
    return factory() if factory else None
