from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_RECENT_MESSAGES = 8
# 中段消息少于这个数就没必要花一次 LLM 调用
MIN_MESSAGES_FOR_LLM_SUMMARY = 4
_CACHE_KEY = "_middle_summary_cache"


def _recent_messages(messages: list[dict[str, str]], max_messages: int = MAX_RECENT_MESSAGES) -> list[dict[str, str]]:
    return messages[-max_messages:]


def extract_initial_user_question(messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") == "user" and message.get("content", "").strip():
            return message["content"].strip()
    return ""


def _summarize_middle_messages(messages: list[dict[str, str]]) -> str:
    """规则版压缩：LLM 不可用时的降级实现。"""
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


def _messages_fingerprint(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _llm_compression_enabled() -> bool:
    try:
        from config import get_settings, llm_is_available
    except ImportError:  # pragma: no cover
        return False
    if not get_settings().get("llm_compression_enabled"):
        return False
    return llm_is_available()


def _summarize_middle_messages_with_llm(messages: list[dict[str, str]], initial_question: str) -> str:
    from agent.llm import invoke_llm
    from prompts import get_middle_summary_prompt

    text = invoke_llm(
        get_middle_summary_prompt(initial_question, messages),
        system="你是一个对话上下文压缩助手，负责在压缩的同时保住关键事实。",
    ).strip()
    return text


def compress_middle_messages(
    state: dict[str, Any],
    middle_messages: list[dict[str, str]],
    initial_question: str,
) -> str:
    """优先 LLM 压缩，失败/关闭/无 LLM 时回落规则实现。

    attach_memory_summary 每轮会被 5 个节点 + append_message 共调用 6 次，
    所以这里按中段消息内容哈希缓存，一轮最多产生 1 次 LLM 调用。
    """
    if not middle_messages:
        return ""

    fallback = _summarize_middle_messages(middle_messages)
    if len(middle_messages) < MIN_MESSAGES_FOR_LLM_SUMMARY or not _llm_compression_enabled():
        return fallback

    fingerprint = _messages_fingerprint(middle_messages)
    cache = state.get(_CACHE_KEY)
    if isinstance(cache, dict) and cache.get("fingerprint") == fingerprint and cache.get("summary"):
        return str(cache["summary"])

    try:
        summary = _summarize_middle_messages_with_llm(middle_messages, initial_question)
    except Exception:
        summary = ""

    if not summary:
        return fallback

    state[_CACHE_KEY] = {"fingerprint": fingerprint, "summary": summary}
    return summary


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
    if state.get("evidence_gaps"):
        parts.append("证据缺口：" + "；".join(state["evidence_gaps"][:5]))
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
    state["conversation_summary_middle"] = compress_middle_messages(state, middle_messages, initial_question)
    state["memory_summary"] = summarize_recent_context(state)
    return state
