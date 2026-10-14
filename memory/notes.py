"""用户主动保存的研究笔记服务。"""
from __future__ import annotations

from typing import Any

from memory.long_term import init_db, save_memory_item

MAX_NOTE_CHARS = 20000


def save_research_note(state: dict[str, Any], content: str, db_path: str) -> None:
    content = content.strip()
    if not content:
        raise ValueError("笔记内容不能为空")
    if len(content) > MAX_NOTE_CHARS:
        raise ValueError(f"笔记最多 {MAX_NOTE_CHARS} 字符，请选取需要保留的内容")
    init_db(db_path)
    user_id = str(state.get("user_id") or "default")
    save_memory_item(
        user_id, state.get("topic") or state.get("initial_user_question", ""),
        "note", content, str(state.get("session_id") or user_id), db_path,
    )
