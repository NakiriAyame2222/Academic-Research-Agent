from __future__ import annotations

from typing import Any

MAX_RECENT_MESSAGES = 8


def _recent_messages(messages: list[dict[str, str]], max_messages: int = MAX_RECENT_MESSAGES) -> list[dict[str, str]]:
    return messages[-max_messages:]


def extract_initial_user_question(messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") == "user" and message.get("content", "").strip():
            return message["content"].strip()
    return ""


def _summarize_middle_messages(messages: list[dict[str, str]]) -> str:
    if not messages:
        return ""
    summary_lines: list[str] = []
    user_messages = [item.get("content", "").strip() for item in messages if item.get("role") == "user" and item.get("content", "").strip()]
    assistant_messages = [item.get("content", "").strip() for item in messages if item.get("role") == "assistant" and item.get("content", "").strip()]
    if user_messages:
        summary_lines.append("中间阶段用户主要问题：" + "；".join(user_messages[:4]))
    if assistant_messages:
        summary_lines.append("中间阶段已回答要点：" + "；".join(text[:120] for text in assistant_messages[:4]))
    return "\n".join(summary_lines)


def summarize_recent_context(state: dict[str, Any]) -> str:
    parts: list[str] = []
    if state.get("topic"):
        parts.append(f"主题：{state['topic']}")
    if state.get("paper_path"):
        parts.append(f"论文：{state['paper_path']}")
    if state.get("selected_papers"):
        titles = [paper.get("title", "") for paper in state["selected_papers"][:5] if paper.get("title")]
        if titles:
            parts.append("候选论文：" + "；".join(titles))
    if state.get("open_questions"):
        parts.append("待解决问题：" + "；".join(state["open_questions"][:5]))
    if state.get("turns"):
        last_turn = state["turns"][-1]
        parts.append(f"最近问题：{last_turn.get('query', '')}")
    if state.get("conversation_summary_middle"):
        parts.append("中间对话压缩摘要：\n" + state["conversation_summary_middle"])
    return "\n".join(parts)


def trim_messages(messages: list[dict[str, str]], max_messages: int = MAX_RECENT_MESSAGES) -> list[dict[str, str]]:
    return _recent_messages(messages, max_messages=max_messages)


def attach_memory_summary(state: dict[str, Any]) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    initial_question = state.get("initial_user_question") or extract_initial_user_question(messages)
    keep_recent = min(MAX_RECENT_MESSAGES, len(messages))
    recent_messages = _recent_messages(messages, keep_recent)

    middle_messages: list[dict[str, str]] = []
    if len(messages) > keep_recent + 1:
        middle_messages = messages[1:-keep_recent]

    state["initial_user_question"] = initial_question
    state["recent_messages"] = recent_messages
    state["conversation_summary_middle"] = _summarize_middle_messages(middle_messages)
    state["memory_summary"] = summarize_recent_context(state)
    return state
