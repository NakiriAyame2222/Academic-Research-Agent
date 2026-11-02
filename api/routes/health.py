from __future__ import annotations

from importlib.util import find_spec

from fastapi import APIRouter, Request

from config import get_settings, llm_is_available, supports_param, take_degrade_notices
from api.schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """后端能力探针：前端据此显示降级提示条（tool calling 被降级等信息不再只躺在终端里）。"""
    settings = get_settings()
    jieba_available = find_spec("jieba") is not None
    worker = getattr(request.app.state, "chroma_worker", None)
    worker_health = worker.health() if worker is not None else {"ready": False, "outbox": {}}
    counts = worker_health.get("outbox", {})
    return HealthResponse(
        llm_available=llm_is_available(),
        tool_calling=bool(settings.get("tool_calling_enabled")) and supports_param("tools"),
        vector_backend=str(settings.get("vector_backend", "")),
        jieba=jieba_available,
        degrade_notices=take_degrade_notices(),
        chroma_ready=bool(worker_health.get("ready")),
        outbox_pending=int(counts.get("pending", 0)),
        outbox_processing=int(counts.get("processing", 0)),
        outbox_retry=int(counts.get("retry", 0)),
        outbox_dead_letter=int(counts.get("dead_letter", 0)),
    )
