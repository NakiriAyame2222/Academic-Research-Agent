"""用户偏好抽取。

改动前只有 memory/long_term.py:extract_preferences_from_task 的固定关键词规则
（"表格"→table、"简洁"→concise……），学不到词表外的东西。
现在：LLM 抽取为主 + 白名单校验，规则结果作为降级与补空。
"""
from __future__ import annotations

from typing import Any

from agent.json_utils import parse_json_object
from memory.long_term import extract_preferences_from_task, merge_preferences

LANGUAGE_VALUES = {"chinese", "english"}
FORMAT_VALUES = {"structured", "table", "markdown", "bullet", "narrative"}
TONE_VALUES = {"academic", "concise", "neutral", "tutorial"}
DEPTH_VALUES = {"overview", "standard", "deep"}

MAX_INTERESTS = 8


def _normalize_choice(value: Any, allowed: set[str]) -> str | None:
    text = str(value or "").strip().lower()
    if not text or text not in allowed:
        return None
    return text


def _normalize_language(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text not in LANGUAGE_VALUES:
        return None
    return "Chinese" if text == "chinese" else "English"


def _normalize_interests(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        return []
    interests = []
    for item in candidates:
        text = str(item).strip().lower()
        if text and len(text) <= 40:
            interests.append(text)
    return sorted(set(interests))[:MAX_INTERESTS]


def validate_preferences(raw: dict[str, Any]) -> dict[str, Any]:
    """只接受白名单取值，防止 LLM 塞进无法被下游消费的自由文本。"""
    validated: dict[str, Any] = {}

    language = _normalize_language(raw.get("language"))
    if language:
        validated["language"] = language

    output_format = _normalize_choice(raw.get("format"), FORMAT_VALUES)
    if output_format:
        validated["format"] = output_format

    tone = _normalize_choice(raw.get("tone"), TONE_VALUES)
    if tone:
        validated["tone"] = tone

    depth = _normalize_choice(raw.get("depth"), DEPTH_VALUES)
    if depth:
        validated["depth"] = depth

    interests = _normalize_interests(raw.get("interests"))
    if interests:
        validated["interests"] = interests

    return validated


def infer_preferences(
    task: str,
    report: str,
    current_preferences: dict[str, Any] | None = None,
    invoke_llm: Any = None,
) -> dict[str, Any]:
    """返回合并后的偏好增量：LLM 优先，规则补空；LLM 不可用时纯规则。"""
    rule_based = extract_preferences_from_task(task, report)

    from config import get_settings, llm_is_available
    from prompts import get_preference_extraction_prompt

    if not get_settings().get("llm_preferences_enabled"):
        return rule_based
    if invoke_llm is None:
        if not llm_is_available():
            return rule_based
        from agent.llm import invoke_llm as default_invoke

        invoke_llm = default_invoke

    try:
        raw = invoke_llm(
            get_preference_extraction_prompt(task, report, current_preferences or {}),
            system="你是一个用户偏好抽取助手，只输出 JSON。",
            json_object=True,
        )
    except Exception:
        return rule_based

    llm_based = validate_preferences(parse_json_object(raw))
    if not llm_based:
        return rule_based
    return merge_preferences(rule_based, llm_based)
