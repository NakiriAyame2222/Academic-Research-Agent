from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_skill(file_path: str) -> dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_all_skills(skills_dir: str) -> list[dict[str, Any]]:
    skill_files = sorted(Path(skills_dir).glob("*.yaml"))
    return [load_skill(str(skill_file)) for skill_file in skill_files]


def select_skill(task: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_task = task.lower()
    best_skill: dict[str, Any] | None = None
    best_score = -1

    for skill in skills:
        keywords = skill.get("match_keywords", [])
        score = sum(1 for keyword in keywords if keyword.lower() in normalized_task)
        if score > best_score:
            best_score = score
            best_skill = skill

    return best_skill or (skills[0] if skills else {})


def get_skill_steps(skill: dict[str, Any]) -> list[str]:
    return list(skill.get("steps", []))
