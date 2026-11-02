from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from memory.compression import attach_memory_summary, trim_messages

CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "data" / "checkpoints"
MAX_RECENT_MESSAGES = 8


def get_session_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    return list(state.get("messages", []))


def append_message(state: dict[str, Any], role: str, content: str) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    messages.append({"role": role, "content": content})
    state["messages"] = messages
    state["recent_messages"] = trim_messages(messages, MAX_RECENT_MESSAGES)
    attach_memory_summary(state)
    return state


def compress_state_for_checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    compressed = {key: value for key, value in state.items() if key not in {"progress_callback", "cancel_event"} and not callable(value)}
    attach_memory_summary(compressed)
    return compressed


def storage_path() -> Path:
    """Resolve the formal DB, preserving legacy tests' checkpoint monkeypatches."""
    explicit = os.getenv("RESEARCH_AGENT_STORAGE_DB", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from config import get_settings

        configured = Path(get_settings().get("storage_db_path") or get_settings()["sqlite_db_path"])
        default_dir = Path(__file__).resolve().parent.parent / "data" / "checkpoints"
        if CHECKPOINT_DIR != default_dir:
            return CHECKPOINT_DIR.parent / "app.db"
        return configured
    except Exception:
        return CHECKPOINT_DIR.parent / "app.db"


def _database():
    from storage import Database

    return Database(storage_path())


def _state_from_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("state_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def save_checkpoint(state: dict[str, Any], session_id: str | None = None) -> None:
    current_session_id = session_id or state.get("session_id") or state.get("user_id", "default")
    snapshot = compress_state_for_checkpoint(state)
    user_id = str(snapshot.get("user_id") or "default")
    topic = str(snapshot.get("topic") or snapshot.get("initial_user_question") or "")
    status = str(snapshot.get("status") or snapshot.get("phase") or "active")
    with _database() as db:
        existing = db.repositories.sessions.get(str(current_session_id))
        if existing is None:
            db.repositories.sessions.create(
                title=topic[:120], status=status, session_id=str(current_session_id),
                user_id=user_id, topic=topic, state=snapshot,
            )
        else:
            db.repositories.sessions.update(
                str(current_session_id), title=topic[:120], status=status,
                user_id=user_id, topic=topic, state=snapshot,
            )


def load_checkpoint(session_id: str, mark_interrupted: bool = False) -> dict[str, Any] | None:
    with _database() as db:
        row = db.repositories.sessions.get(session_id)
    state = _state_from_row(row) if row else {}
    # Compatibility for old fixtures/tests and pre-storage installations. New
    # writes never create this file.
    if not state:
        checkpoint_path = CHECKPOINT_DIR / f"{session_id}.json"
        if not checkpoint_path.exists():
            return None
        try:
            state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    if not isinstance(state, dict):
        return None
    state.setdefault("recent_messages", trim_messages(list(state.get("messages", []))))
    state.setdefault("initial_user_question", "")
    state.setdefault("conversation_summary_middle", "")
    state.setdefault("pending_memory_candidates", [])
    state.setdefault("survey_artifact", {})
    state.setdefault("selected_paper_fulltext_docs", [])
    state.setdefault("exit_requested", False)
    state.setdefault("awaiting_memory_confirmation", False)
    state.setdefault("research_context", {})
    state.setdefault("discussion_intent", "")
    state.setdefault("discussion_history", [])
    state.setdefault("discussion_scope", "")
    state.setdefault("discussion_sources", [])

    # 仅在明确进行异常孤儿任务恢复时，才将未完成的主题标记为 interrupted
    if mark_interrupted:
        dirty = False
        topics = state.get("topics")
        if isinstance(topics, list):
            for t in topics:
                if isinstance(t, dict) and t.get("status") in {"running", "queued"}:
                    t["status"] = "interrupted"
                    dirty = True
        if state.get("phase") in {"collecting", "writing", "initializing", "running", "finalizing"}:
            state["phase"] = "interrupted"
            dirty = True
        if dirty:
            try:
                save_checkpoint(state, session_id)
            except Exception:
                pass

    attach_memory_summary(state)
    return state


def load_or_create_session(session_id: str, initial_state: dict[str, Any]) -> dict[str, Any]:
    state = load_checkpoint(session_id)
    if state is None:
        initial_state["session_id"] = session_id
        initial_state.setdefault("recent_messages", trim_messages(list(initial_state.get("messages", []))))
        attach_memory_summary(initial_state)
        return initial_state
    state.setdefault("session_id", session_id)
    state["resume_from_checkpoint"] = True
    attach_memory_summary(state)
    return state
