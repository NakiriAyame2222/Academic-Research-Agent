from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

ProgressCallback = Callable[[str], None]


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
    callback = state.get("progress_callback")
    if not callable(callback):
        return
    prefix = prefix_builder(stage) if prefix_builder else _default_prefix(stage)
    callback(f"{prefix} {message}")
