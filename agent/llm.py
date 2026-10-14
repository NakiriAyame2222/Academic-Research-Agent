from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from config import RuleBasedLLM, get_llm, llm_is_available, supports_param, take_degrade_notices

# 中转端点可能超时/断连。网络错误在统一入口重试，最终仍失败时必须抛出，
# 让任务状态和进度把网络错误展示给用户，而不是静默走模板或确定性旁路。
LLM_MAX_RETRIES = 10
LLM_RETRY_DELAY_SECONDS = 60.0

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
    "connection reset",
    "connection aborted",
    "broken pipe",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "name resolution",
    "dns",
    "eof",
)
_PERMANENT_ERROR_MARKERS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "incorrect api key",
    "permission denied",
    "forbidden",
    "invalidrequest",
    "invalid request",
    "bad request",
    "model_not_found",
    "model not found",
    "does not exist",
    "configuration",
    "invalid url",
    "missing api key",
    "no api key",
    "unsupported",
    "certificate verify failed",
    "wrong version number",
    "invalid scheme",
)
_LAST_ERROR: list[str] = []
_RETRY_PROGRESS: ContextVar[Any] = ContextVar("llm_retry_progress", default=None)


@contextmanager
def retry_progress(callback):
    token = _RETRY_PROGRESS.set(callback)
    try:
        yield
    finally:
        _RETRY_PROGRESS.reset(token)


class LLMNetworkError(RuntimeError):
    """可恢复网络错误在重试耗尽后的显式失败。"""


def _is_recoverable(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in _PERMANENT_ERROR_MARKERS):
        return False
    # HTTP 5xx/429 和常见连接/超时异常可恢复；4xx 配置、认证和请求错误不可恢复。
    if re.search(r"\b(?:429|500|502|503|504|529)\b", text):
        return True
    return any(marker in text for marker in _NETWORK_ERROR_MARKERS)


def take_last_error() -> str:
    """取出并清空最近一次耗尽重试的 LLM 错误，供进度日志展示。"""
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


def _raise_if_cancelled(cancel_event: Any | None) -> None:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("LLM 调用已取消")


def _call_with_retries(call: Any, cancel_event: Any | None = None) -> Any:
    """执行一次 LLM 请求；RuleBasedLLM 不进入重试，等待可被事件打断。"""
    retries = 0
    while True:
        _raise_if_cancelled(cancel_event)
        try:
            result = call()
            _raise_if_cancelled(cancel_event)
            if retries and callable(_RETRY_PROGRESS.get()):
                _RETRY_PROGRESS.get()("LLM 已恢复")
            return result
        except InterruptedError:
            raise
        except Exception as exc:
            if not _is_recoverable(exc):
                raise
            if retries >= LLM_MAX_RETRIES:
                message = f"LLM 网络错误，已重试 {LLM_MAX_RETRIES} 次仍失败，请检查网络后重新执行。"
                _LAST_ERROR.append(message[:500])
                raise LLMNetworkError(message) from exc
            retries += 1
            if callable(_RETRY_PROGRESS.get()):
                _RETRY_PROGRESS.get()(f"LLM 暂时不可用，{LLM_RETRY_DELAY_SECONDS:g} 秒后重试（{retries}/{LLM_MAX_RETRIES}）")
            # Event.wait 既提供 60 秒等待，也允许取消立即唤醒；无事件时保持普通等待。
            if cancel_event is not None:
                if cancel_event.wait(LLM_RETRY_DELAY_SECONDS):
                    raise InterruptedError("LLM 重试等待期间已取消")
            else:
                import time

                time.sleep(LLM_RETRY_DELAY_SECONDS)


def invoke_llm(
    prompt: str,
    system: str | None = None,
    json_object: bool = False,
    cancel_event: Any | None = None,
) -> str:
    """单轮调用。json_object=True 时尝试用 response_format 约束输出。"""
    llm = get_llm()

    # 演示模型本身就是确定性降级，不应等待 60 秒或产生无意义重试。
    if isinstance(llm, RuleBasedLLM):
        if json_object and supports_param("response_format") and hasattr(llm, "chat"):
            response = llm.chat([], response_format={"type": "json_object"})
            return str(response.get("content") or "")
        return _extract_text(llm.invoke(prompt, system=system))

    if json_object and supports_param("response_format") and hasattr(llm, "chat"):
        def _json_call() -> Any:
            messages: list[dict[str, Any]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            return llm.chat(messages, response_format={"type": "json_object"})

        response = _call_with_retries(_json_call, cancel_event)
        content = str(response.get("content") or "")
        if content:
            return content

    def _invoke_call() -> Any:
        try:
            return _extract_text(llm.invoke(prompt, system=system))
        except TypeError:
            # 兼容不接受 system 参数的替身实现（例如测试里的 fake LLM）
            return _extract_text(llm.invoke(prompt))

    return _call_with_retries(_invoke_call, cancel_event)


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    """多轮 tool calling；不支持 tools 时去掉能力参数但仍请求 LLM。"""
    llm = get_llm()
    if not hasattr(llm, "chat"):
        return {"content": "", "tool_calls": [], "degraded": True}

    if isinstance(llm, RuleBasedLLM):
        return {"content": "", "tool_calls": [], "degraded": True}

    tools_supported = bool(tools) and supports_param("tools")

    def _chat_call() -> Any:
        if tools_supported:
            return llm.chat(messages, tools=tools, tool_choice=tool_choice)
        # 端点已经确认不支持 tools 时，继续发普通 chat 请求，让 LLM 仍参与本轮。
        return llm.chat(messages)

    result = _call_with_retries(_chat_call, cancel_event)
    if tools and not tools_supported:
        result["degraded"] = True
    return result


def tool_calling_available() -> bool:
    from config import get_settings

    if not get_settings().get("tool_calling_enabled"):
        return False
    return llm_is_available() and supports_param("tools")


def drain_degrade_notices() -> list[str]:
    return take_degrade_notices()
