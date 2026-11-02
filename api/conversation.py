"""Web 层多轮对话历史，持久化在 storage.session_messages。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from api.serialize import serialize_state

CONVERSATION_DIR = Path(__file__).resolve().parent.parent / "data" / "conversations"  # legacy test seam
_LOCK = threading.Lock()


def _path(session_id: str) -> Path:
    # session_id 限制为安全字符，防目录穿越（创建端点是 uuid4().hex）
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe or safe != session_id:
        raise ValueError("invalid session id")
    return CONVERSATION_DIR / f"{safe}.json"


def read_conversation(session_id: str) -> list[dict[str, Any]]:
    from memory.short_term import storage_path
    import json
    from storage import Database

    with Database(storage_path()) as db:
        rows = db.repositories.session_messages.list(session_id)
    turns: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if metadata.get("kind") != "conversation_turn":
            continue
        index = int(metadata.get("turn_index", row.get("sequence", 0) // 2))
        turn = turns.setdefault(index, {
            "index": index,
            "query": metadata.get("query", ""),
            "timestamp": metadata.get("timestamp", ""),
            "result": metadata.get("result", {}),
        })
        if row.get("role") == "user":
            turn["query"] = row.get("content", turn["query"])
        elif metadata.get("result"):
            turn["result"] = metadata["result"]
    if turns:
        return [turns[index] for index in sorted(turns)]

    # Read-only compatibility for pre-storage fixtures.
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
    from datetime import datetime
    from memory.short_term import storage_path
    from storage import Database

    turn = {"index": 0, "query": query, "timestamp": datetime.now().isoformat(timespec="seconds"),
            "result": serialize_state(final_state, session_id=session_id)}
    with _LOCK:
        history = read_conversation(session_id)
        turn["index"] = len(history)
        with Database(storage_path()) as db:
            metadata = {"kind": "conversation_turn", "turn_index": turn["index"],
                        "query": query, "timestamp": turn["timestamp"], "result": turn["result"]}
            db.repositories.session_messages.add(
                session_id, "user", query, metadata=metadata,
            )
            db.repositories.session_messages.add(
                session_id, "assistant", str(turn["result"].get("final_answer") or ""), metadata=metadata,
            )
    return turn
