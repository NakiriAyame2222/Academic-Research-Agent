from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

ProgressCallback = Callable[[str], None]
ProgressEventCallback = Callable[[dict[str, Any]], None]


def _default_prefix(stage: str) -> str:
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"[{timestamp}] [{stage}]"


def emit_progress(
    state: dict[str, Any] | None,
    stage: str,
    message: str,
    *,
    prefix_builder: Callable[[str], str] | None = None,
) -> None:
    if state is None:
        return

    # 结构化事件回调（Web 层用）：与字符串回调互不影响，CLI 不设置时不触发。
    event_callback = state.get("progress_event_callback")
    if callable(event_callback):
        event_callback(
            {
                "stage": stage,
                "message": message,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    callback = state.get("progress_callback")
    if not callable(callback):
        return
    prefix = prefix_builder(stage) if prefix_builder else _default_prefix(stage)
    callback(f"{prefix} {message}")
