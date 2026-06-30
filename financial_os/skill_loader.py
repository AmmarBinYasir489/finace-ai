"""Hot-reloadable loader for agent skill files.

Each agent loads its behavior ONLY from financial_os/skills/<agent_name>.md.
Skill files are read from disk on demand and cached by (path, mtime), so editing
a skill file changes agent behavior on the next call with no code change.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

# Canonical agent roster. An agent name not in this set is rejected so a typo
# cannot silently load the wrong (or no) behavior.
AGENTS = frozenset({
    "speech",
    "parser",
    "auditor",
    "memory",
    "rule_engine",
    "loan",
    "shared_expense",
    "budget",
    "insight",
})

# Sections every skill file must define, per the STRICT SKILL FORMAT.
REQUIRED_SECTIONS = (
    "ROLE",
    "RESPONSIBILITIES",
    "FORBIDDEN",
    "INPUT FORMAT",
    "OUTPUT FORMAT",
    "RULES",
    "EXAMPLES",
    "EDGE CASES",
)


class SkillError(RuntimeError):
    """Raised when a skill file is missing, unknown, or malformed."""


@dataclass(frozen=True)
class Skill:
    name: str
    text: str
    path: str
    mtime: float


# Cache: agent_name -> Skill. Invalidated when the file mtime changes.
_cache: dict[str, Skill] = {}


def skill_path(agent_name: str) -> str:
    return os.path.join(_SKILLS_DIR, f"{agent_name}.md")


def load_skill(agent_name: str) -> Skill:
    """Return the skill for an agent, reloading if the file changed on disk."""
    if agent_name not in AGENTS:
        raise SkillError(f"Unknown agent: {agent_name!r}")
    path = skill_path(agent_name)
    if not os.path.exists(path):
        raise SkillError(f"Missing skill file for {agent_name!r}: {path}")
    mtime = os.path.getmtime(path)
    cached = _cache.get(agent_name)
    if cached and cached.mtime == mtime:
        return cached
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    skill = Skill(name=agent_name, text=text, path=path, mtime=mtime)
    _cache[agent_name] = skill
    return skill


def validate_skill(agent_name: str) -> list[str]:
    """Return a list of missing required sections ([] means the file is well-formed).

    A section counts as present when it appears as a header at the start of a line,
    optionally followed by a parenthetical qualifier before the colon
    (e.g. "INPUT FORMAT (recall):").
    """
    skill = load_skill(agent_name)
    missing = []
    for section in REQUIRED_SECTIONS:
        pattern = rf"^{re.escape(section)}\b[^\n]*:"
        if not re.search(pattern, skill.text, flags=re.MULTILINE):
            missing.append(section)
    return missing


def all_agents_present() -> bool:
    return all(os.path.exists(skill_path(name)) for name in AGENTS)
