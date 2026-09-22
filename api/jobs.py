from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable

from api.serialize import serialize_state

# 完成的 job 保留时长，超时回收，避免内存泄漏。
_JOB_TTL_SECONDS = 30 * 60
# stream 轮询 job.events 的间隔；事实源是 job.events，队列信号改用轮询，避免跨线程 Queue 绑定问题。
_POLL_INTERVAL = 0.05


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    session_id: str
    payload: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    events: list[dict[str, Any]] = field(default_factory=list)  # 重连补发用的留档
    closed: bool = False  # 终止帧已入 events（仅在 event loop 线程置位）
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


def _resolve_runner() -> Callable[..., dict[str, Any]]:
    """假模式（RESEARCH_AGENT_FAKE）走 fake_run_research，否则走真实 run_research。"""
    if (os.getenv("RESEARCH_AGENT_FAKE", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        from api.fake import fake_run_research

        return fake_run_research
    from app import run_research

    return run_research


class JobManager:
    """把阻塞的 run_research 丢进线程池，把结构化进度事件投递给 SSE 消费端。

    关键约束：
    - run_research 在工作线程里跑，job.events 只在 event loop 线程里改动 —— 靠
      loop.call_soon_threadsafe 把事件从工作线程搬到 loop 线程：直接 put_nowait 有竞态。
    - 同一 session 的任务串行。
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="research-job")
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifespan ----------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- 提交 / 查询 -------------------------------------------------------
    def submit(self, session_id: str, payload: dict[str, Any]) -> Job:
        self._prune()
        job = Job(id=uuid.uuid4().hex, session_id=session_id, payload=dict(payload))
        self._jobs[job.id] = job
        self._pool.submit(self._run, job)  # 非阻塞：立刻返回，满足 submit → 202
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        """v1 取消：只标记状态并投递终止帧，不硬杀线程（run_research 无协作式取消点）。

        工作线程随后仍会跑完并投递 done/error 帧，_run 末尾检测 CANCELLED 覆盖最终状态；
        前端在收到 cancelled 帧后即关闭流，不再关心后续帧。
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return job
        job.status = JobStatus.CANCELLED
        self._deliver_threadsafe(job, {"type": "cancelled"})
        return job

    def view(self, job: Job) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "session_id": job.session_id,
            "status": job.status.value,
            "events": [self._event_payload(event) for event in job.events if not event.get("type")],
            "result": job.result,
            "error": job.error,
        }

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _prune(self) -> None:
        now = time.time()
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SECONDS
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)

    # -- 工作线程 ----------------------------------------------------------
    def _run(self, job: Job) -> None:
        if job.status == JobStatus.CANCELLED:
            return  # 排队期间就被取消：直接跳过执行
        job.status = JobStatus.RUNNING
        runner = _resolve_runner()
        payload = job.payload
        lock = self._session_lock(job.session_id)
        with lock:
            try:
                final_state = runner(
                    query=payload.get("query", ""),
                    session_id=job.session_id,
                    file_path=payload.get("file_path"),
                    top_k=payload.get("top_k"),
                    rebuild_index=bool(payload.get("rebuild_index", False)),
                    resume=bool(payload.get("resume", False)),
                    progress_event_callback=self._make_event_callback(job),
                )
                if job.status == JobStatus.CANCELLED:
                    return  # 已被取消：丢弃结果，不覆盖 cancelled 状态
                job.result = serialize_state(final_state, session_id=job.session_id)
                job.status = JobStatus.SUCCEEDED
                self._record_turn(job, final_state)
                self._deliver_threadsafe(job, {"type": "done"})
            except Exception as exc:  # noqa: BLE001 —— 必须兜底，否则前端永远转圈
                if job.status == JobStatus.CANCELLED:
                    return
                job.error = str(exc)
                job.status = JobStatus.FAILED
                self._deliver_threadsafe(job, {"type": "error", "message": str(exc)})
            finally:
                job.finished_at = time.time()

    def _make_event_callback(self, job: Job) -> Callable[[dict[str, Any]], None]:
        def callback(event: dict[str, Any]) -> None:
            self._deliver_threadsafe(job, dict(event))

        return callback

    def _record_turn(self, job: Job, final_state: dict[str, Any]) -> None:
        """多轮对话记账：失败不影响任务结果本身。"""
        try:
            from api.conversation import append_turn

            append_turn(job.session_id, str(job.payload.get("query", "")), final_state)
        except Exception:
            pass

    def _deliver_threadsafe(self, job: Job, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            raise RuntimeError("JobManager loop 未绑定：请在 FastAPI lifespan 里调用 bind_loop")
        loop.call_soon_threadsafe(self._deliver, job, event)

    def _deliver(self, job: Job, event: dict[str, Any]) -> None:
        # 只在 event loop 线程改 job.events；先 append 再置 closed，保证 closed=True 时终止帧已在册。
        stored = {"id": len(job.events), **event}
        job.events.append(stored)
        if event.get("type") in {"done", "error", "cancelled"}:
            job.closed = True

    # -- SSE 消费 ----------------------------------------------------------
    async def stream(self, job: Job, last_event_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """SSE 帧生成器。job.events 是唯一事实源（按序号读）：

        - 首次连接 last_event_id=None，从头补发历史再续流。
        - 重连时 EventSource 自动带 Last-Event-ID，只补发该 id 之后的事件。
        """
        cursor = (last_event_id + 1) if last_event_id is not None else 0
        while True:
            while cursor < len(job.events):
                event = job.events[cursor]
                cursor += 1
                yield self._to_sse(event)
                if event.get("type") in {"done", "error", "cancelled"}:
                    return
            if job.closed and cursor >= len(job.events):
                return  # 重连到已结束的 job：补完剩余后收尾，不空转
            await asyncio.sleep(_POLL_INTERVAL)

    def _event_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event.get("id", 0),
            "stage": event.get("stage", ""),
            "message": event.get("message", ""),
            "timestamp": event.get("timestamp", ""),
        }

    def _to_sse(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("id", 0))
        kind = event.get("type")
        if kind == "done":
            return {"event": "done", "id": event_id, "data": json.dumps({"status": "succeeded"}, ensure_ascii=False)}
        if kind == "cancelled":
            return {
                "event": "cancelled",
                "id": event_id,
                "data": json.dumps({"status": "cancelled"}, ensure_ascii=False),
            }
        if kind == "error":
            return {
                "event": "error",
                "id": event_id,
                "data": json.dumps({"status": "failed", "message": event.get("message", "")}, ensure_ascii=False),
            }
        return {
            "event": "progress",
            "id": event_id,
            "data": json.dumps(self._event_payload(event), ensure_ascii=False),
        }
