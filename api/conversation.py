"""Web 层的多轮对话历史。

run_research 的 checkpoint 只存 user 消息（assistant 回复不落 messages），
对话流由这里单独记账：data/conversations/<sid>.json，每轮 append。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from api.serialize import serialize_state

CONVERSATION_DIR = Path(__file__).resolve().parent.parent / "data" / "conversations"
_LOCK = threading.Lock()


def _path(session_id: str) -> Path:
    # session_id 限制为安全字符，防目录穿越（创建端点是 uuid4().hex）
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe or safe != session_id:
        raise ValueError("invalid session id")
    return CONVERSATION_DIR / f"{safe}.json"


def read_conversation(session_id: str) -> list[dict[str, Any]]:
    path = _path(session_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def append_turn(
    session_id: str,
    query: str,
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """任务成功后追加一轮对话（query + 序列化结果）。线程内调用，自带锁。"""
    turn = {
        "index": 0,
        "query": query,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "result": serialize_state(final_state, session_id=session_id),
    }
    with _LOCK:
        history = read_conversation(session_id)
        turn["index"] = len(history)
        history.append(turn)
        CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
        _path(session_id).write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return turn
