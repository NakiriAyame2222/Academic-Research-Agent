from __future__ import annotations

import json
import time
from typing import Any

from agent.json_utils import parse_json_object
from agent.llm import chat_with_tools, drain_degrade_notices, tool_calling_available
from agent.llm import retry_progress
from agent.progress import emit_progress
from tools.registry import ToolContext, ToolSpec, dispatch, serialize_result, to_openai_tools

DEFAULT_MAX_STEPS = 8


def _summarize_result(result: Any) -> str:
    if isinstance(result, dict):
        if "error" in result:
            return f"error={result['error']}"
        for key in ("count", "indexed_papers", "finished"):
            if key in result:
                return f"{key}={result[key] if key != 'indexed_papers' else len(result[key])}"
    text = serialize_result(result)
    return text[:120]


def run_tool_loop(
    state: dict[str, Any],
    specs: list[ToolSpec],
    context: ToolContext,
    system_prompt: str,
    goal_prompt: str,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """执行工具循环，返回 {degraded, steps, trace, finish_reason}。"""
    if not tool_calling_available():
        for notice in drain_degrade_notices():
            emit_progress(state, "tool", notice)
        emit_progress(state, "tool", "未启用原生 tool calling（无可用 LLM 或端点不支持），改用确定性取证流程")
        return {"degraded": True, "steps": 0, "trace": [], "finish_reason": ""}

    tools_payload = to_openai_tools(specs)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal_prompt},
    ]
    trace: list[dict[str, Any]] = []

    cancel_ev = getattr(context, "cancel_event", None)

    for step in range(1, max_steps + 1):
        if cancel_ev and getattr(cancel_ev, "is_set", lambda: False)():
            return {"degraded": False, "cancelled": True, "steps": step - 1, "trace": trace, "finish_reason": "cancelled"}

        with retry_progress(lambda message: emit_progress(state, "tool", message)):
            try:
                result = chat_with_tools(messages, tools=tools_payload, cancel_event=cancel_ev)
            except TypeError:
                # 兼容测试替身和第三方旧适配器仍使用旧签名的情况。
                result = chat_with_tools(messages, tools=tools_payload)
        for notice in drain_degrade_notices():
            emit_progress(state, "tool", notice)

        if cancel_ev and getattr(cancel_ev, "is_set", lambda: False)():
            return {"degraded": False, "cancelled": True, "steps": step - 1, "trace": trace, "finish_reason": "cancelled"}

        if result.get("degraded"):
            if result.get("error"):
                emit_progress(state, "tool", f"tool calling 调用失败，改用确定性流程：{result['error']}")
            else:
                emit_progress(state, "tool", "端点不支持 tool calling，改用确定性取证流程")
            return {"degraded": True, "steps": step - 1, "trace": trace, "finish_reason": ""}

        tool_calls = result.get("tool_calls") or []
        content = str(result.get("content") or "")

        if not tool_calls:
            emit_progress(state, "tool", f"第 {step} 步：模型判断无需继续调用工具")
            return {
                "degraded": False,
                "steps": step,
                "trace": trace,
                "finish_reason": content[:400] or context.finish_reason,
            }

        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": call["id"] or f"call_{step}_{index}",
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
                    }
                    for index, call in enumerate(tool_calls)
                ],
            }
        )

        cancelled_during_calls = False
        for index, call in enumerate(tool_calls):
            call_id = call.get("id") or f"call_{step}_{index}"
            if cancel_ev and getattr(cancel_ev, "is_set", lambda: False)():
                cancelled_during_calls = True
                # 严格保证 LLM tool_call message pairing：取消也要配对
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": serialize_result({"error": "operation cancelled"}),
                    }
                )
                continue

            name = call["name"]
            arguments = parse_json_object(call["arguments"] or "{}")
            emit_progress(state, "tool", f"第 {step} 步：调用 {name}({serialize_result(arguments)[:160]})")

            started = time.perf_counter()
            tool_result = dispatch(specs, name, arguments, context)
            elapsed = time.perf_counter() - started

            trace.append(
                {
                    "step": step,
                    "tool": name,
                    "arguments": arguments,
                    "result_summary": _summarize_result(tool_result),
                    "elapsed_seconds": round(elapsed, 3),
                }
            )
            emit_progress(state, "tool", f"第 {step} 步：{name} 返回 {_summarize_result(tool_result)}（{elapsed:.2f}s）")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": serialize_result(tool_result),
                }
            )

        if cancelled_during_calls or (cancel_ev and getattr(cancel_ev, "is_set", lambda: False)()):
            return {"degraded": False, "cancelled": True, "steps": step, "trace": trace, "finish_reason": "cancelled"}

        if context.finished:
            emit_progress(state, "tool", f"取证结束：{context.finish_reason}")
            return {"degraded": False, "steps": step, "trace": trace, "finish_reason": context.finish_reason}

    emit_progress(state, "tool", f"达到最大工具调用轮数 {max_steps}，进入分析阶段")
    return {"degraded": False, "steps": max_steps, "trace": trace, "finish_reason": "达到最大工具调用轮数"}


def trace_to_text(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return "本轮未产生工具调用。"
    lines = []
    for item in trace:
        lines.append(f"- step {item['step']} {item['tool']}({json.dumps(item['arguments'], ensure_ascii=False)}) -> {item['result_summary']}")
    return "\n".join(lines)
