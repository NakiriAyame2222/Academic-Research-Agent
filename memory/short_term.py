from __future__ import annotations

import json
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
    compressed = {key: value for key, value in state.items() if key != "progress_callback" and not callable(value)}
    attach_memory_summary(compressed)
    return compressed


def save_checkpoint(state: dict[str, Any], session_id: str | None = None) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    current_session_id = session_id or state.get("session_id") or state.get("user_id", "default")
    checkpoint_path = CHECKPOINT_DIR / f"{current_session_id}.json"
    snapshot = compress_state_for_checkpoint(state)
    checkpoint_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(session_id: str) -> dict[str, Any] | None:
    checkpoint_path = CHECKPOINT_DIR / f"{session_id}.json"
    if not checkpoint_path.exists():
        return None
    state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    state.setdefault("recent_messages", trim_messages(list(state.get("messages", []))))
    state.setdefault("initial_user_question", "")
    state.setdefault("conversation_summary_middle", "")
    state.setdefault("pending_memory_candidates", [])
    state.setdefault("survey_artifact", {})
    state.setdefault("selected_paper_fulltext_docs", [])
    state.setdefault("exit_requested", False)
    state.setdefault("awaiting_memory_confirmation", False)
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
