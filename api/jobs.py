from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable

from api.serialize import serialize_state

logger = logging.getLogger(__name__)

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
    scheduler: Any | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    phase: str = ""
    topics: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)


def _resolve_runner() -> Callable[..., dict[str, Any]]:
    """假模式（RESEARCH_AGENT_FAKE）走 fake_run_research，否则走真实 run_research。"""
    if (os.getenv("RESEARCH_AGENT_FAKE", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        from api.fake import fake_run_research

        return fake_run_research
    from agent.runtime import run_research

    return run_research


class JobManager:
    """把阻塞的 run_research / TopicScheduler 丢进线程池，把结构化进度事件投递给 SSE 消费端。

    关键约束：
    - run_research 在工作线程里跑，job.events 只在 event loop 线程里改动 —— 靠
      loop.call_soon_threadsafe 把事件从工作线程搬到 loop 线程：直接 put_nowait 有竞态。
    - 同一 session 的任务串行。
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="research-job")
        self._session_locks: dict[str, threading.Lock] = {}
        self._admission_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._resume_journal: dict[tuple[str, str], dict[str, Any]] = {}

    # -- lifespan ----------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def shutdown(self) -> None:
        for job in self._jobs.values():
            if job.scheduler:
                try:
                    job.scheduler.shutdown()
                except Exception:
                    pass
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

    def get_active_job_for_session(self, session_id: str) -> Job | None:
        for job in reversed(list(self._jobs.values())):
            if job.session_id == session_id and job.status in {JobStatus.PENDING, JobStatus.RUNNING}:
                return job
        return None

    def get_latest_job_for_session(self, session_id: str) -> Job | None:
        for job in reversed(list(self._jobs.values())):
            if job.session_id == session_id:
                return job
        return None

    def _admission_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._admission_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._admission_locks[session_id] = lock
            return lock

    def submit_command(self, target_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        cmd_id = str(payload.get("command_id") or uuid.uuid4().hex)
        session_id = target_id
        target_job = self._jobs.get(target_id)
        if target_job is not None:
            session_id = target_job.session_id

        journal_key = (session_id, cmd_id)
        cmd_name = str(payload.get("command", "")).strip().lower()

        if cmd_name in {"resume", "continue"}:
            admission_lock = self._admission_lock(session_id)
            with admission_lock:
                if journal_key in self._resume_journal:
                    return copy.deepcopy(self._resume_journal[journal_key])

                from memory.short_term import load_checkpoint, save_checkpoint

                cp = load_checkpoint(session_id)
                if cp and "resume_journal" in cp and cmd_id in cp["resume_journal"]:
                    saved_res = cp["resume_journal"][cmd_id]
                    self._resume_journal[journal_key] = copy.deepcopy(saved_res)
                    return saved_res

                active_job = self.get_active_job_for_session(session_id)
                if active_job is not None:
                    res = {
                        "command_id": cmd_id,
                        "command_type": "resume",
                        "status": "accepted",
                        "message": "调研任务已在恢复运行中",
                        "job_id": active_job.id,
                        "phase": getattr(active_job, "phase", "collecting") or "collecting",
                        "clarification_options": [],
                    }
                    cmd_entry = {
                        "command_id": cmd_id,
                        "command_type": "resume",
                        "command": "resume",
                        "status": "accepted",
                        "job_id": active_job.id,
                        "timestamp": time.time(),
                        "message": "调研任务已在恢复运行中",
                    }
                    if getattr(active_job, "scheduler", None) is not None:
                        with active_job.scheduler._lock:
                            active_job.scheduler.resume_journal[cmd_id] = copy.deepcopy(res)
                            from agent.topic_scheduler import CommandRecord
                            cmd_record = CommandRecord.from_dict(cmd_entry)
                            active_job.scheduler.commands.append(cmd_record)
                            active_job.scheduler.seen_command_ids.add(cmd_id)
                            try:
                                active_job.scheduler._persist_state_locked()
                            except Exception as exc:
                                active_job.scheduler.resume_journal.pop(cmd_id, None)
                                if cmd_record in active_job.scheduler.commands:
                                    active_job.scheduler.commands.remove(cmd_record)
                                active_job.scheduler.seen_command_ids.discard(cmd_id)
                                logger.error(f"Failed to persist live scheduler resume command: {exc}")
                                return {
                                    "command_id": cmd_id,
                                    "command_type": "resume",
                                    "status": "rejected",
                                    "message": f"恢复指令持久化失败: {exc}",
                                    "phase": getattr(active_job, "phase", "collecting") or "collecting",
                                    "clarification_options": [],
                                }
                    else:
                        if cp:
                            cp.setdefault("resume_journal", {})[cmd_id] = res
                            cp.setdefault("commands", []).append(cmd_entry)
                            try:
                                save_checkpoint(cp, session_id)
                            except Exception as exc:
                                cp.get("resume_journal", {}).pop(cmd_id, None)
                                if cp.get("commands") and cp["commands"][-1].get("command_id") == cmd_id:
                                    cp["commands"].pop()
                                logger.error(f"Failed to persist active job checkpoint: {exc}")
                                return {
                                    "command_id": cmd_id,
                                    "command_type": "resume",
                                    "status": "rejected",
                                    "message": f"恢复指令持久化失败: {exc}",
                                    "phase": getattr(active_job, "phase", "collecting") or "collecting",
                                    "clarification_options": [],
                                }
                    self._resume_journal[journal_key] = res
                    return res

                if cp and str(cp.get("phase", "")).lower() in {"interrupted", "collecting", "writing", "finalizing"}:
                    new_job = self.submit(
                        session_id,
                        {
                            "query": cp.get("initial_query") or cp.get("query", ""),
                            "resume": True,
                            "explicit_resume": True,
                            "command_id": cmd_id,
                        },
                    )
                    res = {
                        "command_id": cmd_id,
                        "command_type": "resume",
                        "status": "accepted",
                        "message": "已恢复中断的调研任务",
                        "job_id": new_job.id,
                        "phase": "collecting",
                        "clarification_options": [],
                    }
                    cmd_entry = {
                        "command_id": cmd_id,
                        "command_type": "resume",
                        "command": "resume",
                        "status": "accepted",
                        "job_id": new_job.id,
                        "timestamp": time.time(),
                        "message": "已恢复中断的调研任务",
                    }
                    cp.setdefault("resume_journal", {})[cmd_id] = res
                    cp.setdefault("commands", []).append(cmd_entry)
                    try:
                        save_checkpoint(cp, session_id)
                    except Exception as exc:
                        new_job.status = JobStatus.CANCELLED
                        self._jobs.pop(new_job.id, None)
                        cp.get("resume_journal", {}).pop(cmd_id, None)
                        if cp.get("commands") and cp["commands"][-1].get("command_id") == cmd_id:
                            cp["commands"].pop()
                        logger.error(f"Failed to persist resume journal: {exc}")
                        return {
                            "command_id": cmd_id,
                            "command_type": "resume",
                            "status": "rejected",
                            "message": f"恢复指令持久化失败: {exc}",
                            "phase": str(cp.get("phase", "interrupted")),
                            "clarification_options": [],
                        }
                    self._resume_journal[journal_key] = res
                    return res

        job = target_job or self.get_active_job_for_session(session_id)
        if job is None or getattr(job, "scheduler", None) is None:
            return {
                "command_id": cmd_id,
                "status": "rejected",
                "message": "未找到进行中或可操作的调研任务",
                "phase": "closed",
                "clarification_options": [],
            }
        record = job.scheduler.submit_command(payload)
        return record.to_dict()

    def cancel(self, job_id: str) -> Job | None:
        """取消任务：设置取消标记，协同终止调度器及各子主题工作线程，并投递终止帧。"""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return job
        job.status = JobStatus.CANCELLED
        job.cancel_event.set()
        if getattr(job, "scheduler", None) is not None:
            try:
                job.scheduler.cancel_run()
            except Exception:
                pass
        self._deliver_threadsafe(job, {"type": "cancelled"})
        return job

    def view(self, job: Job) -> dict[str, Any]:
        phase = getattr(job, "phase", "")
        topics = list(getattr(job, "topics", []))
        commands = list(getattr(job, "commands", []))
        if getattr(job, "scheduler", None) is not None:
            snap = job.scheduler.get_state_snapshot()
            phase = snap.get("phase", phase)
            topics = snap.get("topics", topics)
            commands = snap.get("commands", commands)
        return {
            "job_id": job.id,
            "session_id": job.session_id,
            "status": job.status.value,
            "phase": phase,
            "topics": topics,
            "commands": commands,
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
        with self._admission_lock(job.session_id):
            pass
        if job.status == JobStatus.CANCELLED:
            return
        lock = self._session_lock(job.session_id)
        with lock:
            if job.status == JobStatus.CANCELLED:
                return
            job.status = JobStatus.RUNNING
            payload = job.payload
            try:
                from agent.runtime import detect_intent_mode, resolve_file_path
                from agent.report_discussion import classify_follow_up, is_completed_report_state
                from memory.short_term import load_checkpoint
                from config import get_settings

                file_path = resolve_file_path(payload.get("query", ""), payload.get("file_path"))
                intent_mode = detect_intent_mode(payload.get("query", ""), file_path)

                previous_state = load_checkpoint(job.session_id) if payload.get("resume") else None
                if not file_path and is_completed_report_state(previous_state) and classify_follow_up(payload.get("query", "")) == "new-research":
                    intent_mode = "topic_research"
                is_discussion = (
                    not file_path and not payload.get("explicit_resume") and payload.get("command") != "resume"
                    and is_completed_report_state(previous_state)
                    and classify_follow_up(payload.get("query", "")) != "new-research"
                )
                if is_discussion:
                    from agent.runtime import run_report_discussion
                    fake = (os.getenv("RESEARCH_AGENT_FAKE", "") or "").strip().lower() in {"1", "true", "yes", "on"}
                    final_state = run_report_discussion(
                        payload.get("query", ""), previous_state,
                        top_k=payload.get("top_k"),
                        progress_event_callback=self._make_event_callback(job),
                        cancel_event=job.cancel_event,
                        invoke=(lambda prompt, **kw: "模拟报告讨论：基于已完成报告与论文笔记的回答。") if fake else None,
                    )

                elif intent_mode == "paper_qa":
                    runner = _resolve_runner()
                    final_state = runner(
                        query=payload.get("query", ""),
                        session_id=job.session_id,
                        file_path=file_path,
                        top_k=payload.get("top_k"),
                        rebuild_index=bool(payload.get("rebuild_index", False)),
                        resume=bool(payload.get("resume", False)),
                        progress_event_callback=self._make_event_callback(job),
                        output_path=str(get_settings()["reports_dir"] / f"{job.id}.md"),
                        cancel_event=job.cancel_event,
                    )
                else:
                    from agent.topic_scheduler import RunPhase, TopicScheduler
                    from memory.short_term import load_checkpoint

                    fake_mode = (os.getenv("RESEARCH_AGENT_FAKE", "") or "").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
                    scheduler = TopicScheduler(
                        session_id=job.session_id,
                        initial_query=payload.get("query", ""),
                        user_id=payload.get("user_id", "default"),
                        concurrency_limit=payload.get("concurrency_limit"),
                        progress_event_callback=self._make_event_callback(job),
                        fake_mode=fake_mode,
                        top_k=int(payload.get("top_k") or get_settings()["default_top_k"]),
                    )
                    job.scheduler = scheduler
                    if job.status == JobStatus.CANCELLED:
                        scheduler.cancel_run("任务在启动前已被取消")
                        return

                    is_resume = bool(payload.get("resume", False))
                    is_interrupted_continuation = False
                    cp = None
                    if is_resume:
                        cp = load_checkpoint(job.session_id)
                        if cp and cp.get("topics"):
                            cp_phase = str(cp.get("phase", ""))
                            is_explicit = bool(payload.get("explicit_resume") or payload.get("command") == "resume")
                            query_matches = (not payload.get("query")) or (payload.get("query") == cp.get("initial_query"))
                            if is_explicit or (cp_phase in {"interrupted", "collecting", "writing", "finalizing"} and query_matches):
                                is_interrupted_continuation = True
                                if not scheduler.initial_query:
                                    scheduler.initial_query = str(cp.get("initial_query") or cp.get("query") or "")

                    if is_interrupted_continuation and cp:
                        from agent.topic_scheduler import SubtopicState, CommandRecord

                        if cp.get("run_id"):
                            scheduler.run_id = str(cp["run_id"])
                        scheduler.user_id = str(cp.get("user_id") or scheduler.user_id)
                        scheduler.user_profile = copy.deepcopy(cp.get("user_profile") or {})
                        scheduler.top_k = int(cp.get("top_k") or scheduler.top_k)
                        scheduler.pipeline_mode = str(cp.get("pipeline_mode") or scheduler.pipeline_mode)
                        scheduler.stage_timings = copy.deepcopy(cp.get("stage_timings") or {})
                        scheduler.candidate_artifact_paths = copy.deepcopy(cp.get("candidate_artifact_paths") or {})
                        scheduler.global_selection = copy.deepcopy(cp.get("selection") or {})
                        scheduler.report_review = copy.deepcopy(cp.get("report_review") or {})
                        scheduler.topics = [SubtopicState.from_dict(t) for t in cp.get("topics", [])]
                        scheduler.commands = [
                            CommandRecord.from_dict(c) if isinstance(c, dict) else c
                            for c in cp.get("commands", [])
                        ]
                        scheduler.seen_command_ids = {
                            getattr(c, "command_id", "")
                            for c in scheduler.commands
                            if getattr(c, "command_id", "")
                        }
                        if cp.get("resume_journal"):
                            scheduler.resume_journal = copy.deepcopy(cp["resume_journal"])
                        scheduler.phase = RunPhase.INTERRUPTED
                        scheduler.resume_interrupted()
                    else:
                        if cp and cp.get("resume_journal"):
                            scheduler.resume_journal = copy.deepcopy(cp["resume_journal"])
                        scheduler.start()

                    final_state = scheduler.wait_until_complete()
                    if scheduler.resume_journal and "resume_journal" not in final_state:
                        final_state["resume_journal"] = copy.deepcopy(scheduler.resume_journal)
                    job.phase = (
                        scheduler.phase.value
                        if isinstance(scheduler.phase, RunPhase)
                        else str(scheduler.phase)
                    )
                    job.topics = [t.to_dict() for t in scheduler.topics]
                    job.commands = [c.to_dict() for c in scheduler.commands]

                    if scheduler.phase == RunPhase.CANCELLED or job.status == JobStatus.CANCELLED:
                        job.status = JobStatus.CANCELLED
                        self._deliver_threadsafe(job, {"type": "cancelled"})
                        return

                    if scheduler.phase == RunPhase.FAILED:
                        raise RuntimeError(scheduler.error or "主题调研任务失败")

                if job.status == JobStatus.CANCELLED:
                    return

                job.result = serialize_state(final_state, session_id=job.session_id)
                job.status = JobStatus.SUCCEEDED
                self._record_turn(job, final_state)
                self._deliver_threadsafe(job, {"type": "done"})
            except Exception as exc:  # noqa: BLE001
                if job.status == JobStatus.CANCELLED:
                    return
                job.error = str(exc)
                job.status = JobStatus.FAILED
                self._deliver_threadsafe(job, {"type": "error", "message": str(exc)})
            finally:
                job.finished_at = time.time()
                if getattr(job, "scheduler", None) is not None:
                    try:
                        job.scheduler._pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass

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
        payload: dict[str, Any] = {
            "id": event.get("id", 0),
            "stage": event.get("stage", ""),
            "message": event.get("message", ""),
            "timestamp": event.get("timestamp", ""),
        }
        for key in (
            "topic_id",
            "topic_name",
            "topic_status",
            "command_id",
            "phase",
            "details",
            "options",
        ):
            if key in event and event[key] is not None:
                payload[key] = event[key]
        return payload

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
