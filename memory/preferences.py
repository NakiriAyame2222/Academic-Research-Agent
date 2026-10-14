from __future__ import annotations

import re
from typing import Any


def output_preferences_for_query(query: str) -> dict[str, Any]:
    """仅识别明确的本轮输出要求；不写入长期偏好，也不调用模型。"""
    result: dict[str, Any] = {}
    patterns = {
        "language": [(r"(?:请用|使用|用|以)\s*中文|(?:answer|respond|write)\s+in\s+chinese", "Chinese"),
                     (r"(?:请用|使用|用|以)\s*英文|(?:answer|respond|write)\s+in\s+english", "English")],
        "format": [(r"(?:请用|使用|用|以)\s*表格|(?:as|in)\s+a\s+table", "table"),
                   (r"(?:请用|使用|用|以)\s*markdown", "markdown")],
        "tone": [(r"(?:请|请用|使用|用)\s*简洁|(?:keep\s+it|be)\s+concise", "concise")],
    }
    for field, choices in patterns.items():
        for pattern, value in choices:
            if re.search(pattern, query, re.IGNORECASE):
                result[field] = value
                break
    return result
