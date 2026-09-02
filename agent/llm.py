"""LLM 调用的统一入口。

改动前只有 agent/nodes.py:_invoke_llm 一个私有函数，且所有角色设定都塞在
唯一一条 user message 里。这里统一支持：
- 真正的 system role
- response_format={"type":"json_object"}（端点不支持时由 config.py 自动脱参）
- 原生 tool calling（chat 返回 tool_calls）
"""
from __future__ import annotations

from typing import Any

from config import get_llm, llm_is_available, supports_param, take_degrade_notices

# 中转端点可能超时/断连。改动前 _invoke_llm 没有任何异常处理，一次网络抖动就会
# 让整张图崩在中途；现在统一在这里吞掉网络类异常并返回空串，
# 各调用点已有的 fallback（解析失败走模板、笔记回落原文等）随之生效。
_NETWORK_ERROR_MARKERS = (
    "apiconnectionerror",
    "apitimeouterror",
    "connecterror",
    "connectionerror",
    "readtimeout",
    "timeout",
    "ssl",
    "internalservererror",
    "rate_limit",
    "ratelimit",
)
_LAST_ERROR: list[str] = []


def _is_recoverable(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _NETWORK_ERROR_MARKERS)


def take_last_error() -> str:
    """取出并清空最近一次被吞掉的 LLM 调用错误，供进度日志展示。"""
    if not _LAST_ERROR:
        return ""
    return _LAST_ERROR.pop()


def _extract_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    if hasattr(response, "text"):
        return str(response.text)
    return str(response)


def invoke_llm(prompt: str, system: str | None = None, json_object: bool = False) -> str:
    """单轮调用。json_object=True 时尝试用 response_format 约束输出。"""
    llm = get_llm()

    if json_object and supports_param("response_format") and hasattr(llm, "chat"):
        try:
            messages: list[dict[str, Any]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            content = str(llm.chat(messages, response_format={"type": "json_object"}).get("content") or "")
            if content:
                return content
        except Exception as exc:
            if not _is_recoverable(exc):
                raise
            _LAST_ERROR.append(f"{type(exc).__name__}: {exc}"[:200])
            return ""

    try:
        try:
            return _extract_text(llm.invoke(prompt, system=system))
        except TypeError:
            # 兼容不接受 system 参数的替身实现（例如测试里的 fake LLM）
            return _extract_text(llm.invoke(prompt))
    except Exception as exc:
        if not _is_recoverable(exc):
            raise
        _LAST_ERROR.append(f"{type(exc).__name__}: {exc}"[:200])
        return ""


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> dict[str, Any]:
    """多轮 tool calling。端点不支持 tools 时返回 degraded=True。"""
    llm = get_llm()
    if not hasattr(llm, "chat"):
        return {"content": "", "tool_calls": [], "degraded": True}
    if tools and not supports_param("tools"):
        return {"content": "", "tool_calls": [], "degraded": True}
    try:
        result = llm.chat(messages, tools=tools, tool_choice=tool_choice if tools else None)
    except Exception as exc:
        return {"content": "", "tool_calls": [], "degraded": True, "error": f"{type(exc).__name__}: {exc}"}
    if tools and not supports_param("tools"):
        result["degraded"] = True
    return result


def tool_calling_available() -> bool:
    from config import get_settings

    if not get_settings().get("tool_calling_enabled"):
        return False
    return llm_is_available() and supports_param("tools")


def drain_degrade_notices() -> list[str]:
    return take_degrade_notices()
