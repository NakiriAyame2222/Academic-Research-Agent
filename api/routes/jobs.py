from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from api.deps import get_job_manager
from api.jobs import JobManager

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    manager: JobManager = Depends(get_job_manager),
) -> EventSourceResponse:
    """SSE 事件流：progress / done / error。ping=15s 保活，防中间层读超时断流。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    raw = request.headers.get("last-event-id")
    last_event_id = int(raw) if raw and raw.isdigit() else None
    return EventSourceResponse(manager.stream(job, last_event_id), ping=15)


@router.get("/jobs/{job_id}")
def job_view(job_id: str, manager: JobManager = Depends(get_job_manager)) -> dict:
    """轮询兜底：返回 {status, events, result, error}。前端收到 done 后来这里取完整 result。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return manager.view(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, manager: JobManager = Depends(get_job_manager)) -> dict:
    """v1 取消：标记 cancelled 并推终止帧；线程继续跑完但结果被丢弃。"""
    job = manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job.id, "status": job.status.value}
