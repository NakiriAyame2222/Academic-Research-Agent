"""LLM 结构化输出的容错解析。

模型常把 JSON 包在 ```json 围栏里，或在 JSON 前后附一段说明文字。
裸 json.loads 会直接失败并静默走 fallback 模板，所以这里统一做：
剥围栏 -> 截取首个 {/[ 到末个 }/] -> json.loads。
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def strip_code_fence(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    match = _FENCE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text


def _slice_between(text: str, open_char: str, close_char: str) -> str:
    start = text.find(open_char)
    end = text.rfind(close_char)
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def parse_json_object(raw: str) -> dict[str, Any]:
    text = strip_code_fence(raw)
    parsed = _loads(text)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item

    sliced = _slice_between(text, "{", "}")
    if sliced:
        parsed = _loads(sliced)
        if isinstance(parsed, dict):
            return parsed
    return {}


def parse_json_array(raw: str) -> list[Any]:
    text = strip_code_fence(raw)
    parsed = _loads(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                return value

    sliced = _slice_between(text, "[", "]")
    if sliced:
        parsed = _loads(sliced)
        if isinstance(parsed, list):
            return parsed
    return []


def parse_json_dicts(raw: str) -> list[dict[str, Any]]:
    return [item for item in parse_json_array(raw) if isinstance(item, dict)]


def parse_string_list(raw: str) -> list[str]:
    """解析字符串列表；JSON 失败时退化为按行读取（去掉 markdown 列表符号）。"""
    items = parse_json_array(raw)
    if items:
        values = [str(item).strip() for item in items if str(item).strip()]
        if values:
            return values

    lines: list[str] = []
    for line in strip_code_fence(raw).splitlines():
        stripped = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip().strip('",')
        if stripped and not stripped.startswith(("{", "}", "[", "]")):
            lines.append(stripped)
    return lines
