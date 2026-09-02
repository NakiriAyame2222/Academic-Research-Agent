from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# yaml 里的 output_style 之前从未被读取，与 SQLite 的 user_preferences.tone 是两套
# 互不相干的机制。这里把它映射到同一套输出偏好上（用户显式偏好优先级更高）。
OUTPUT_STYLE_PREFERENCES: dict[str, dict[str, str]] = {
    "academic": {"tone": "academic", "format": "structured"},
    "structured": {"tone": "neutral", "format": "structured"},
    "concise": {"tone": "concise", "format": "bullet"},
    "table": {"tone": "neutral", "format": "table"},
}


def load_skill(file_path: str) -> dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_all_skills(skills_dir: str) -> list[dict[str, Any]]:
    skill_files = sorted(Path(skills_dir).glob("*.yaml"))
    return [load_skill(str(skill_file)) for skill_file in skill_files]


def score_skill(task: str, skill: dict[str, Any]) -> float:
    """归一化命中率，避免关键词列得越多的 skill 越容易赢。"""
    keywords = [str(keyword) for keyword in skill.get("match_keywords", []) if str(keyword).strip()]
    if not keywords:
        return 0.0
    normalized_task = task.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in normalized_task)
    if hits == 0:
        return 0.0
    # 命中数为主、命中率为辅：命中 2 个的一定胜过命中 1 个的
    return hits + hits / len(keywords)


def select_skill(task: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    """关键词全不命中时返回空技能。

    改动前 best_score 初始化为 -1，任何 skill 都以 0 分"胜出"，而 load_all_skills
    是字母序 glob，结果是关键词全不命中时永远选中 literature_review。
    """
    best_skill: dict[str, Any] | None = None
    best_score = 0.0

    for skill in skills:
        score = score_skill(task, skill)
        if score > best_score:
            best_score = score
            best_skill = skill

    return best_skill or {}


def get_skill_steps(skill: dict[str, Any]) -> list[str]:
    return list(skill.get("steps", []))


def get_skill_description(skill: dict[str, Any]) -> str:
    return str(skill.get("description", "") or "")


def get_skill_output_preferences(skill: dict[str, Any]) -> dict[str, str]:
    """把 output_style 翻译成输出偏好；未知取值不产生影响。"""
    style = str(skill.get("output_style", "") or "").strip().lower()
    return dict(OUTPUT_STYLE_PREFERENCES.get(style, {}))
