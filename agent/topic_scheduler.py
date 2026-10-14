from __future__ import annotations

from agent import research_service
from memory.preferences import output_preferences_for_query

import copy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable
import uuid

from agent.citation_check import enforce_citations
from agent.deep_research_pipeline import (
    CANDIDATE_CAP_PER_SUBTOPIC,
    GLOBAL_SELECTED_CAP,
    build_candidate_artifact,
    candidate_artifact_to_json,
    candidate_artifact_to_markdown,
    normalize_and_dedupe_candidates,
    parse_report_review_response,
    parse_selector_response,
    stage_timing,
)
from agent.llm import LLMNetworkError, invoke_llm, retry_progress
from agent.research_artifacts import dedupe_papers, select_top_papers
from agent.report_discussion import build_research_context
from agent.tool_loop import DEFAULT_MAX_STEPS, run_tool_loop
from config import get_settings
from memory.long_term import get_user_preferences, init_db
from memory.short_term import save_checkpoint
from prompts import (
    TOPIC_AGENT_SYSTEM,
    get_topic_agent_goal_prompt,
)
from rag.retriever import (
    assign_citation_ids,
    build_citations,
    get_session_scope_dir,
    load_vector_store,
    register_evidence,
    retrieve_documents,
)
from tools.registry import ToolContext, get_toolset
from tools.search import build_selected_papers_index, search_academic_topic, search_metadata_candidates

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 1
DEFAULT_MAX_RETRIES = 2
MAX_FULLTEXT_PAPERS_PER_TOPIC = 2
DEFAULT_OUTLINE = [
    "背景与核心问题",
    "代表方法与关键进展",
    "实验与结果对比",
    "开放问题与未来方向",
]


class TopicStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunPhase(str, Enum):
    INITIALIZING = "initializing"
    COLLECTING = "collecting"
    WRITING = "writing"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CommandType(str, Enum):
    ADD_TOPIC = "add_topic"
    CANCEL_TOPIC = "cancel_topic"
    CANCEL_RUN = "cancel_run"
    RESUME = "resume"


class CommandStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CLARIFICATION_NEEDED = "clarification_needed"
    IGNORED = "ignored"


@dataclass
class SubtopicState:
    id: str
    name: str
    status: TopicStatus = TopicStatus.QUEUED
    error: str = ""
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    generation: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    # 各主题独立取证产物
    papers: list[dict[str, Any]] = field(default_factory=list)
    selected_papers: list[dict[str, Any]] = field(default_factory=list)
    paper_notes: list[dict[str, Any]] = field(default_factory=list)
    fulltext_notes: list[dict[str, Any]] = field(default_factory=list)
    evidence_pool: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence_status: list[dict[str, Any]] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    reflection: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    tool_loop_degraded: bool = False
    vector_persist_dir: str | None = None
    latest_progress: str = ""
    research_rounds: int = 1
    query_batches: list[dict[str, Any]] = field(default_factory=list)
    candidate_artifact_path: str = ""
    stage_timings: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 运行期协作取消标记（不序列化）
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, TopicStatus) else str(self.status),
            "error": self.error,
            "retry_count": self.retry_count,
            "generation": self.generation,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "papers": copy.deepcopy(self.papers),
            "selected_papers": copy.deepcopy(self.selected_papers),
            "paper_notes": copy.deepcopy(self.paper_notes),
            "fulltext_notes": copy.deepcopy(self.fulltext_notes),
            "evidence_pool": copy.deepcopy(self.evidence_pool),
            "citations": copy.deepcopy(self.citations),
            "evidence_status": copy.deepcopy(self.evidence_status),
            "evidence_gaps": copy.deepcopy(self.evidence_gaps),
            "reflection": copy.deepcopy(self.reflection),
            "tool_trace": copy.deepcopy(self.tool_trace),
            "tool_loop_degraded": self.tool_loop_degraded,
            "vector_persist_dir": self.vector_persist_dir,
            "latest_progress": self.latest_progress,
            "research_rounds": self.research_rounds,
            "query_batches": copy.deepcopy(self.query_batches),
            "candidate_artifact_path": self.candidate_artifact_path,
            "stage_timings": copy.deepcopy(self.stage_timings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubtopicState:
        status_val = data.get("status", "queued")
        try:
            status = TopicStatus(status_val)
        except ValueError:
            status = TopicStatus.QUEUED
        inst = cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            status=status,
            error=str(data.get("error", "")),
            retry_count=int(data.get("retry_count", 0)),
            generation=int(data.get("generation", 0)),
            created_at=float(data.get("created_at", time.time())),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            papers=copy.deepcopy(list(data.get("papers", []))),
            selected_papers=copy.deepcopy(list(data.get("selected_papers", []))),
            paper_notes=copy.deepcopy(list(data.get("paper_notes", []))),
            fulltext_notes=copy.deepcopy(list(data.get("fulltext_notes", []))),
            evidence_pool=copy.deepcopy(list(data.get("evidence_pool", []))),
            citations=copy.deepcopy(list(data.get("citations", []))),
            evidence_status=copy.deepcopy(list(data.get("evidence_status", []))),
            evidence_gaps=copy.deepcopy(list(data.get("evidence_gaps", []))),
            reflection=copy.deepcopy(dict(data.get("reflection", {}))),
            tool_trace=copy.deepcopy(list(data.get("tool_trace", []))),
            tool_loop_degraded=bool(data.get("tool_loop_degraded", False)),
            vector_persist_dir=data.get("vector_persist_dir"),
            latest_progress=str(data.get("latest_progress", "")),
            research_rounds=int(data.get("research_rounds", 1)),
            query_batches=copy.deepcopy(list(data.get("query_batches", []))),
            candidate_artifact_path=str(data.get("candidate_artifact_path", "")),
            stage_timings=copy.deepcopy(dict(data.get("stage_timings", {}))),
        )
        return inst


@dataclass
class CommandRecord:
    command_id: str
    command_type: str
    raw_input: str
    status: CommandStatus
    message: str
    topic_id: str | None = None
    topic_name: str | None = None
    phase: str = ""
    timestamp: float = field(default_factory=time.time)
    clarification_options: list[dict[str, str]] = field(default_factory=list)
    target_topic_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if not self.topic_id and self.target_topic_id:
            self.topic_id = self.target_topic_id
        if not self.target_topic_id and self.topic_id:
            self.target_topic_id = self.topic_id

    def to_dict(self) -> dict[str, Any]:
        data = {
            "command_id": self.command_id,
            "command_type": self.command_type,
            "command": self.command_type,
            "raw_input": self.raw_input,
            "status": self.status.value if isinstance(self.status, CommandStatus) else str(self.status),
            "message": self.message,
            "topic_id": self.topic_id,
            "target_topic_id": self.topic_id,
            "topic_name": self.topic_name,
            "phase": self.phase,
            "timestamp": self.timestamp,
            "clarification_options": copy.deepcopy(self.clarification_options),
        }
        if self.job_id:
            data["job_id"] = self.job_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRecord:
        status_val = data.get("status", "accepted")
        if isinstance(status_val, CommandStatus):
            status = status_val
        else:
            try:
                status = CommandStatus(str(status_val))
            except ValueError:
                status = CommandStatus.ACCEPTED
        tid = data.get("topic_id") or data.get("target_topic_id")
        cmd_type = str(data.get("command_type") or data.get("command") or "")
        job_id = data.get("job_id")
        return cls(
            command_id=str(data.get("command_id", "")),
            command_type=cmd_type,
            raw_input=str(data.get("raw_input", "")),
            status=status,
            message=str(data.get("message", "")),
            topic_id=str(tid) if tid is not None else None,
            target_topic_id=str(tid) if tid is not None else None,
            topic_name=data.get("topic_name"),
            phase=str(data.get("phase", "")),
            timestamp=float(data.get("timestamp", time.time())),
            clarification_options=copy.deepcopy(list(data.get("clarification_options", []))),
            job_id=str(job_id) if job_id is not None else None,
        )


# --- 自然语言控制指令解析 ----------------------------------------------------

_ADD_PATTERNS = [
    re.compile(r"^(?:增加|添加|新增|调研)\s*(?:调研)?\s*(?:子)?主题\s*[:：]?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:add|research)\s*(?:sub)?topic\s*[:：]?\s*(.+)$", re.IGNORECASE),
]

_CANCEL_PATTERNS = [
    re.compile(r"^(?:取消|停止|终止|移除|删除)\s*(?:调研)?\s*(?:子)?主题\s*[:：]?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:cancel|stop|remove|delete)\s*(?:sub)?topic\s*[:：]?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:取消|停止)\s*([a-zA-Z0-9_\-一-鿿]+)$", re.IGNORECASE),
    re.compile(r"^(?:cancel|stop)\s*([a-zA-Z0-9_\-]+)$", re.IGNORECASE),
]

_CANCEL_RUN_PATTERNS = [
    re.compile(r"^(?:取消|停止|终止)\s*(?:当前)?\s*(?:研究|调研|任务|运行)$", re.IGNORECASE),
    re.compile(r"^(?:cancel|stop)\s*(?:all|run|research|task)$", re.IGNORECASE),
]


def _evaluate_topic_cancellation(
    t: SubtopicState, current_phase: Any
) -> tuple[CommandType, dict[str, Any], CommandStatus, str, list[dict[str, str]]]:
    if t.status == TopicStatus.CANCELLED:
        return CommandType.CANCEL_TOPIC, {"topic_id": t.id, "topic_name": t.name}, CommandStatus.IGNORED, f"主题《{t.name}》（{t.id}）已处于取消状态，无需重复操作", []
    phase_val = current_phase.value if isinstance(current_phase, RunPhase) else str(current_phase or "")
    is_intake_open = phase_val in {"initializing", "collecting", ""}
    if t.status in {TopicStatus.COMPLETED, TopicStatus.FAILED}:
        if is_intake_open:
            return CommandType.CANCEL_TOPIC, {"topic_id": t.id, "topic_name": t.name}, CommandStatus.ACCEPTED, f"已取消主题并从本轮报告移除：{t.name}", []
        return CommandType.CANCEL_TOPIC, {"topic_id": t.id, "topic_name": t.name}, CommandStatus.REJECTED, f"准入已关闭，无法从报告移除主题：{t.name}", []
    return CommandType.CANCEL_TOPIC, {"topic_id": t.id, "topic_name": t.name}, CommandStatus.ACCEPTED, f"已取消主题：{t.name}", []


def parse_control_command(
    raw_input: str | dict[str, Any],
    current_topics: list[SubtopicState],
    phase: RunPhase,
) -> tuple[CommandType | None, dict[str, Any], CommandStatus, str, list[dict[str, str]]]:
    """解析控制命令，精确校验目标并处理歧义。"""
    active_topics = [t for t in current_topics if t.status in {TopicStatus.QUEUED, TopicStatus.RUNNING}]

    # 1. 结构化 JSON 输入
    if isinstance(raw_input, dict):
        cmd = str(raw_input.get("command") or "").strip().lower()
        if cmd in {"add_topic", "add"}:
            topic_val = str(raw_input.get("topic_name") or raw_input.get("topic") or raw_input.get("name") or "").strip()
            if not topic_val:
                return CommandType.ADD_TOPIC, {}, CommandStatus.REJECTED, "新增主题内容不能为空", []
            return CommandType.ADD_TOPIC, {"topic": topic_val}, CommandStatus.ACCEPTED, f"已接收添加主题：{topic_val}", []
        if cmd in {"cancel_topic", "cancel"}:
            target_id = str(raw_input.get("target") or raw_input.get("topic_id") or raw_input.get("id") or "").strip()
            target_name = str(raw_input.get("topic_name") or raw_input.get("topic") or raw_input.get("name") or "").strip()
            target = target_id or target_name
            if not target:
                if len(active_topics) > 1:
                    return (
                        CommandType.CANCEL_TOPIC,
                        {},
                        CommandStatus.CLARIFICATION_NEEDED,
                        "请指明要取消的主题",
                        [{"id": t.id, "name": t.name} for t in active_topics],
                    )
                if len(active_topics) == 1:
                    t = active_topics[0]
                    return _evaluate_topic_cancellation(t, phase)
                return CommandType.CANCEL_TOPIC, {}, CommandStatus.REJECTED, "当前没有正在进行的主题可取消", []

            # 优先精确匹配 ID
            exact_id = [t for t in current_topics if t.id.lower() == target.lower()]
            if len(exact_id) == 1:
                return _evaluate_topic_cancellation(exact_id[0], phase)

            # 精确匹配名称
            exact_name = [t for t in current_topics if t.name.lower() == target.lower()]
            if len(exact_name) > 1:
                return (
                    CommandType.CANCEL_TOPIC,
                    {},
                    CommandStatus.CLARIFICATION_NEEDED,
                    f"与「{target}」精确匹配到多个同名主题，请指定具体主题 ID",
                    [{"id": t.id, "name": t.name} for t in exact_name],
                )
            if len(exact_name) == 1:
                return _evaluate_topic_cancellation(exact_name[0], phase)

            # 模糊/子串匹配
            matched = [t for t in current_topics if target.lower() in t.name.lower() or target.lower() in t.id.lower()]
            if not matched:
                return CommandType.CANCEL_TOPIC, {}, CommandStatus.REJECTED, f"未找到与「{target}」匹配的主题", []
            if len(matched) > 1:
                return (
                    CommandType.CANCEL_TOPIC,
                    {},
                    CommandStatus.CLARIFICATION_NEEDED,
                    f"与「{target}」匹配到多个候选主题，请指定具体主题 ID 或名称",
                    [{"id": t.id, "name": t.name} for t in matched],
                )
            return _evaluate_topic_cancellation(matched[0], phase)

        if cmd in {"cancel_run", "stop_run"}:
            return CommandType.CANCEL_RUN, {}, CommandStatus.ACCEPTED, "正在取消整体调研任务", []
        if cmd in {"resume", "continue"}:
            return CommandType.RESUME, {}, CommandStatus.ACCEPTED, "正在恢复中断的调研任务", []
        raw_text = str(raw_input.get("raw_text") or raw_input.get("command") or "").strip()
    else:
        raw_text = str(raw_input or "").strip()

    if not raw_text:
        return None, {}, CommandStatus.REJECTED, "指令内容不能为空", []

    # 2. 自然语言正则匹配
    for pat in _CANCEL_RUN_PATTERNS:
        if pat.match(raw_text):
            return CommandType.CANCEL_RUN, {}, CommandStatus.ACCEPTED, "已接收取消整个调研任务指令", []

    for pat in _ADD_PATTERNS:
        m = pat.match(raw_text)
        if m:
            topic_val = m.group(1).strip().strip("\"'“”")
            if not topic_val:
                return CommandType.ADD_TOPIC, {}, CommandStatus.REJECTED, "新增主题内容不能为空", []
            return CommandType.ADD_TOPIC, {"topic": topic_val}, CommandStatus.ACCEPTED, f"已接收增加调研主题：{topic_val}", []

    for pat in _CANCEL_PATTERNS:
        m = pat.match(raw_text)
        if m:
            target = m.group(1).strip().strip("\"'“”")
            if not target:
                if len(active_topics) > 1:
                    return (
                        CommandType.CANCEL_TOPIC,
                        {},
                        CommandStatus.CLARIFICATION_NEEDED,
                        "请指明要取消的主题",
                        [{"id": t.id, "name": t.name} for t in active_topics],
                    )
                if len(active_topics) == 1:
                    t = active_topics[0]
                    return _evaluate_topic_cancellation(t, phase)
                return CommandType.CANCEL_TOPIC, {}, CommandStatus.REJECTED, "当前没有正在进行的主题可取消", []

            # 优先精确匹配 ID
            exact_id = [t for t in current_topics if t.id.lower() == target.lower()]
            if len(exact_id) == 1:
                return _evaluate_topic_cancellation(exact_id[0], phase)

            # 精确匹配名称
            exact_name = [t for t in current_topics if t.name.lower() == target.lower()]
            if len(exact_name) > 1:
                return (
                    CommandType.CANCEL_TOPIC,
                    {},
                    CommandStatus.CLARIFICATION_NEEDED,
                    f"与「{target}」精确匹配到多个同名主题，请指定具体主题 ID",
                    [{"id": t.id, "name": t.name} for t in exact_name],
                )
            if len(exact_name) == 1:
                return _evaluate_topic_cancellation(exact_name[0], phase)

            # 模糊/子串匹配
            matched = [t for t in current_topics if target.lower() in t.name.lower() or target.lower() in t.id.lower()]
            if not matched:
                return CommandType.CANCEL_TOPIC, {}, CommandStatus.REJECTED, f"未找到与「{target}」匹配的主题", []
            if len(matched) > 1:
                return (
                    CommandType.CANCEL_TOPIC,
                    {},
                    CommandStatus.CLARIFICATION_NEEDED,
                    f"与「{target}」匹配到多个候选主题，请指定具体主题 ID 或名称",
                    [{"id": t.id, "name": t.name} for t in matched],
                )
            return _evaluate_topic_cancellation(matched[0], phase)

    return None, {}, CommandStatus.REJECTED, f"无法识别的指令：{raw_text}", []


# --- 调度器实现 --------------------------------------------------------------

class TopicScheduler:
    """多主题异步取证与交互式调度器（同一实例严格串行）。"""

    def __init__(
        self,
        session_id: str,
        initial_query: str,
        user_id: str = "default",
        concurrency_limit: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
        progress_event_callback: Callable[[dict[str, Any]], None] | None = None,
        fake_mode: bool = False,
        top_k: int = 4,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.session_id = session_id
        self.initial_query = initial_query
        self.run_id = str(run_id or kwargs.get("run_id") or uuid.uuid4().hex[:12])
        self.user_id = user_id
        # Web 主题调研的串行约束必须在共享调度器入口收口，不能由 API、CLI
        # 或恢复路径传入的 concurrency_limit 绕过。参数保留仅为兼容旧调用方。
        self.concurrency_limit = DEFAULT_CONCURRENCY
        self.progress_callback = progress_callback
        self.progress_event_callback = progress_event_callback
        self.fake_mode = fake_mode
        self.top_k = top_k

        self.phase = RunPhase.INITIALIZING
        self.topics: list[SubtopicState] = []
        self.commands: list[CommandRecord] = []
        self.seen_command_ids: set[str] = set()

        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._intake_closed = False
        self._pool = ThreadPoolExecutor(max_workers=DEFAULT_CONCURRENCY)
        self._active_workers: dict[str, Future] = {}

        self.search_plan: dict[str, Any] = {}
        self.final_state: dict[str, Any] = {}
        self._draft_final_state: dict[str, Any] | None = None
        # New deep-research flow: subtopic workers collect metadata only; the
        # parent run performs one global selection and one shared full-text
        # indexing pass after all candidate pools are available.
        self.pipeline_mode = str(kwargs.get("pipeline_mode") or "metadata")
        self.stage_timings: dict[str, dict[str, Any]] = {}
        self.candidate_artifact_paths: dict[str, str] = {}
        self.global_selection: dict[str, Any] = {}
        self.report_review: dict[str, Any] = {}
        self.revision_applied = False
        self.resume_journal: dict[str, Any] = copy.deepcopy(kwargs.get("resume_journal") or {})
        self.user_profile: dict[str, Any] = {}
        self.error: str = ""

    def _next_topic_id_locked(self) -> str:
        existing_indices = [
            int(t.id.split("-")[-1])
            for t in self.topics
            if t.id.startswith("topic-") and t.id.split("-")[-1].isdigit()
        ]
        next_idx = (max(existing_indices) + 1) if existing_indices else (len(self.topics) + 1)
        return f"topic-{next_idx}"

    def emit(
        self,
        stage: str,
        message: str,
        topic_id: str | None = None,
        topic_name: str | None = None,
        topic_status: str | None = None,
        command_id: str | None = None,
        phase: str | None = None,
        details: dict[str, Any] | None = None,
        options: list[dict[str, str]] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "stage": stage,
            "message": message,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if topic_id:
            event["topic_id"] = topic_id
        if topic_name:
            event["topic_name"] = topic_name
        if topic_status:
            event["topic_status"] = topic_status
        if command_id:
            event["command_id"] = command_id
        current_phase = phase or (self.phase.value if isinstance(self.phase, RunPhase) else str(self.phase))
        event["phase"] = current_phase
        if details:
            event["details"] = details
        if options:
            event["options"] = options

        if self.progress_event_callback:
            try:
                self.progress_event_callback(event)
            except Exception as exc:
                logger.warning(f"progress_event_callback error: {exc}")

        if self.progress_callback:
            prefix = f"[{stage}]"
            if topic_name:
                prefix += f" [{topic_name}]"
            try:
                self.progress_callback(f"{prefix} {message}")
            except Exception:
                pass

    def _persist_state_locked(self) -> None:
        """原子落盘当前运行态（供断点续跑和重启发现）。"""
        try:
            snapshot = self.get_state_snapshot()
            save_checkpoint(snapshot, self.session_id)
        except Exception as exc:
            logger.error(f"Failed to persist state snapshot: {exc}")
            raise

    # -- 启动与规划 ------------------------------------------------------------

    def start(self, initial_subtopics: list[str] | None = None) -> None:
        self._load_output_preferences()
        topic_pipeline = getattr(self, "_run_topic_pipeline", None)
        pipeline_hooked = getattr(topic_pipeline, "__func__", None) is not TopicScheduler._run_topic_pipeline
        if getattr(run_tool_loop, "__module__", "agent.tool_loop") != "agent.tool_loop" or pipeline_hooked:
            self.pipeline_mode = "legacy"
        topics_to_add = initial_subtopics or []
        if not topics_to_add:
            topics_to_add = self._plan_initial_topics()

        with self._lock:
            if self.phase in {RunPhase.CANCELLED, RunPhase.FAILED}:
                return
            self.phase = RunPhase.COLLECTING
            self.emit("scheduler", f"开始调研任务：{self.initial_query}", phase="collecting")

            for name in topics_to_add:
                tid = self._next_topic_id_locked()
                subtopic = SubtopicState(id=tid, name=name, status=TopicStatus.QUEUED)
                self.topics.append(subtopic)
                self.emit("scheduler", f"子主题已就绪入队：{name} ({tid})", topic_id=tid, topic_name=name, topic_status="queued")

            try:
                self._persist_state_locked()
            except Exception:
                pass
            self._schedule_next_locked()

    def _load_output_preferences(self) -> None:
        if self.fake_mode or self.user_profile:
            return
        db_path = str(get_settings()["sqlite_db_path"])
        init_db(db_path)
        self.user_profile = {**get_user_preferences(self.user_id, db_path),
                             **output_preferences_for_query(self.initial_query)}

    def _plan_initial_topics(self) -> list[str]:
        if self.fake_mode:
            return [
                f"{self.initial_query} - 核心机制",
                f"{self.initial_query} - 代表方法",
                f"{self.initial_query} - 评测对比",
            ]

        try:
            from agent.nodes import _build_topic_search_plan

            state = {
                "initial_user_question": self.initial_query,
                "current_query": self.initial_query,
                "user_id": self.user_id,
                "progress_callback": self.progress_callback,
                "progress_event_callback": self.progress_event_callback,
                "cancel_event": self._cancel_event,
            }
            plan = _build_topic_search_plan(state, self.initial_query, self.user_profile)
            self.search_plan = plan
            subtopics = [str(item).strip() for item in plan.get("subtopics", []) if str(item).strip()]
            if subtopics:
                return subtopics[:4]
        except LLMNetworkError:
            # LLM 统一入口已完成 10 次网络重试；不能再走规划模板静默绕过。
            raise
        except Exception as exc:
            self.emit("scheduler", f"子主题规划自动拆解降级：{exc}")

        return [self.initial_query]

    # -- 命令调度与处理 --------------------------------------------------------

    def submit_command(self, payload: dict[str, Any]) -> CommandRecord:
        cmd_id = str(payload.get("command_id") or uuid.uuid4().hex)

        with self._lock:
            # 幂等性：已处理过的 command_id 直接返回
            if cmd_id in self.seen_command_ids:
                for existing in self.commands:
                    if existing.command_id == cmd_id:
                        return existing

            cmd_type, args, status, message, options = parse_control_command(
                payload, self.topics, self.phase
            )

            # 准入关闭状态下拒绝任何新增与修改（取消全任务仍允许）
            if cmd_type != CommandType.CANCEL_RUN and (self._intake_closed or self.phase in {
                RunPhase.WRITING,
                RunPhase.FINALIZING,
                RunPhase.SUCCEEDED,
                RunPhase.CANCELLED,
                RunPhase.FAILED,
            }):
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value if cmd_type else "unknown",
                    raw_input=str(payload),
                    status=CommandStatus.REJECTED,
                    message=f"调研已进入撰写/完成阶段（当前: {self.phase.value}），无法再接收修改主题命令",
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)
                self.emit("command", record.message, command_id=cmd_id, phase=self.phase.value)
                return record

            # 处理澄清需求
            if status == CommandStatus.CLARIFICATION_NEEDED:
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value if cmd_type else "unknown",
                    raw_input=str(payload),
                    status=status,
                    message=message,
                    phase=self.phase.value,
                    clarification_options=options,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)
                try:
                    self._persist_state_locked()
                except Exception:
                    pass
                self.emit("clarification", message, command_id=cmd_id, options=options)
                return record

            if status != CommandStatus.ACCEPTED:
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value if cmd_type else "unknown",
                    raw_input=str(payload),
                    status=status,
                    message=message,
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)
                self.emit("command", message, command_id=cmd_id)
                return record

            # 执行已接受指令
            if cmd_type == CommandType.ADD_TOPIC:
                topic_name = args.get("topic") or args.get("topic_name") or args.get("name") or "新主题"
                new_id = self._next_topic_id_locked()
                subtopic = SubtopicState(id=new_id, name=topic_name, status=TopicStatus.QUEUED)
                self.topics.append(subtopic)

                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value,
                    raw_input=str(payload),
                    status=CommandStatus.ACCEPTED,
                    message=f"已成功添加调研主题：{topic_name}（ID: {new_id}）",
                    topic_id=new_id,
                    topic_name=topic_name,
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)

                # 持久化先于确认返回
                try:
                    self._persist_state_locked()
                except Exception as exc:
                    self.topics.remove(subtopic)
                    self.commands.remove(record)
                    self.seen_command_ids.remove(cmd_id)
                    return CommandRecord(
                        command_id=cmd_id,
                        command_type=cmd_type.value,
                        raw_input=str(payload),
                        status=CommandStatus.REJECTED,
                        message=f"状态持久化失败: {exc}",
                        phase=self.phase.value,
                    )

                self.emit(
                    "command",
                    record.message,
                    topic_id=new_id,
                    topic_name=topic_name,
                    topic_status="queued",
                    command_id=cmd_id,
                )
                self._schedule_next_locked()
                return record

            if cmd_type == CommandType.CANCEL_TOPIC:
                target_id = args.get("topic_id")
                topic_obj = next((t for t in self.topics if t.id == target_id), None)
                if topic_obj is None:
                    record = CommandRecord(
                        command_id=cmd_id,
                        command_type=cmd_type.value,
                        raw_input=str(payload),
                        status=CommandStatus.REJECTED,
                        message=f"未找到目标主题 ID: {target_id}",
                        phase=self.phase.value,
                    )
                    self.commands.append(record)
                    self.seen_command_ids.add(cmd_id)
                    return record

                self._cancel_single_topic_locked(topic_obj, reason="用户指令取消")
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value,
                    raw_input=str(payload),
                    status=CommandStatus.ACCEPTED,
                    message=f"已取消主题：{topic_obj.name}（ID: {topic_obj.id}）",
                    topic_id=topic_obj.id,
                    topic_name=topic_obj.name,
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)

                try:
                    self._persist_state_locked()
                except Exception as exc:
                    record.status = CommandStatus.REJECTED
                    record.message = f"主题已在内存中取消，但状态持久化失败: {exc}"

                self.emit(
                    "command",
                    record.message,
                    topic_id=topic_obj.id,
                    topic_name=topic_obj.name,
                    topic_status="cancelled",
                    command_id=cmd_id,
                )
                self._schedule_next_locked()
                self._check_intake_and_finalize_locked()
                return record

            if cmd_type == CommandType.CANCEL_RUN:
                self.cancel_run(reason="用户请求取消整体任务")
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value,
                    raw_input=str(payload),
                    status=CommandStatus.ACCEPTED,
                    message="正在终止整体调研任务",
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)
                try:
                    self._persist_state_locked()
                except Exception as exc:
                    record.status = CommandStatus.REJECTED
                    record.message = f"整体调研已在内存中取消，但状态持久化失败: {exc}"
                return record

            if cmd_type == CommandType.RESUME:
                self.resume_interrupted()
                record = CommandRecord(
                    command_id=cmd_id,
                    command_type=cmd_type.value,
                    raw_input=str(payload),
                    status=CommandStatus.ACCEPTED,
                    message="已开始恢复未完成的主题调研",
                    phase=self.phase.value,
                )
                self.commands.append(record)
                self.seen_command_ids.add(cmd_id)
                try:
                    self._persist_state_locked()
                except Exception:
                    pass
                return record

            return CommandRecord(
                command_id=cmd_id,
                command_type="unknown",
                raw_input=str(payload),
                status=CommandStatus.REJECTED,
                message="未知命令",
                phase=self.phase.value,
            )

    def _cancel_single_topic_locked(self, topic: SubtopicState, reason: str = "") -> None:
        """精确取消单主题：发出取消信号并使已分配的代次失效。已完成/失败的主题取消后从本轮报告排除。"""
        if topic.status == TopicStatus.CANCELLED:
            return

        topic.status = TopicStatus.CANCELLED
        topic.finished_at = time.time()
        topic.latest_progress = f"已取消：{reason or '用户取消'}"
        topic._cancel_event.set()
        topic.generation += 1

        self.emit(
            "topic",
            f"子主题《{topic.name}》已取消并从报告排除",
            topic_id=topic.id,
            topic_name=topic.name,
            topic_status="cancelled",
        )

    def cancel_run(self, reason: str = "取消任务") -> None:
        """取消全调研运行（单调终态防护）。"""
        with self._lock:
            if self.phase in {RunPhase.SUCCEEDED, RunPhase.FAILED, RunPhase.CANCELLED}:
                return
            self.phase = RunPhase.CANCELLED
            self._cancel_event.set()
            self.final_state = {}
            self._draft_final_state = None
            for topic in self.topics:
                self._cancel_single_topic_locked(topic, reason=reason)
            try:
                self._persist_state_locked()
            except Exception:
                pass
            self.emit("scheduler", f"全任务已取消：{reason}", phase="cancelled")

    # -- 工作线程调度与执行 ----------------------------------------------------

    def _schedule_next_locked(self) -> None:
        """在受控并发约束下启动排队主题。"""
        if self.phase != RunPhase.COLLECTING:
            return

        while len(self._active_workers) < self.concurrency_limit:
            queued = next(
                (t for t in self.topics if t.status == TopicStatus.QUEUED and t.id not in self._active_workers),
                None,
            )
            if not queued:
                break

            queued.status = TopicStatus.RUNNING
            queued.started_at = time.time()
            queued.generation += 1
            curr_gen = queued.generation

            self.emit(
                "topic",
                f"启动子主题取证：{queued.name}",
                topic_id=queued.id,
                topic_name=queued.name,
                topic_status="running",
            )

            future = self._pool.submit(self._worker_entry, queued, curr_gen)
            self._active_workers[queued.id] = future
            future.add_done_callback(lambda done, tid=queued.id: self._worker_done(tid, done))

    def _worker_done(self, topic_id: str, future: Future) -> None:
        """Future 完成后才释放容量，旧回调不能移除新一代 worker。"""
        with self._lock:
            if self._active_workers.get(topic_id) is not future:
                return
            self._active_workers.pop(topic_id)
            self._schedule_next_locked()
            self._check_intake_and_finalize_locked()

    def _worker_entry(self, topic: SubtopicState, generation: int) -> None:
        with retry_progress(lambda message: self.emit("llm", message, topic_id=topic.id, topic_name=topic.name)):
            self._worker_entry_with_progress(topic, generation)

    def _worker_entry_with_progress(self, topic: SubtopicState, generation: int) -> None:
        """工作线程包装，负责捕获异常、非锁退避重试，以及状态提交。"""
        try:
            # Keep the old worker path available to callers that explicitly
            # replace the legacy tool loop (the compatibility test seam and
            # local integrations do this).  Normal production runs use the
            # metadata-only pipeline; they never need the old tool loop here.
            topic_pipeline = getattr(self, "_run_topic_pipeline", None)
            legacy_hooked = (
                getattr(run_tool_loop, "__module__", "agent.tool_loop") != "agent.tool_loop"
                or getattr(topic_pipeline, "__func__", None) is not TopicScheduler._run_topic_pipeline
            )
            if self.pipeline_mode == "metadata" and not self.fake_mode and not legacy_hooked:
                self._run_metadata_topic_pipeline(topic, generation)
            else:
                self._run_topic_pipeline(topic, generation)
        except InterruptedError:
            with self._lock:
                if topic.generation == generation and topic.status != TopicStatus.CANCELLED:
                    topic.status = TopicStatus.CANCELLED
                    topic.finished_at = time.time()
                    topic.latest_progress = "取证已取消"
                    try:
                        self._persist_state_locked()
                    except Exception:
                        pass
            return
        except Exception as exc:
            should_retry = False
            retry_delay = 0.0
            with self._lock:
                if topic.generation != generation or topic._cancel_event.is_set():
                    return
                # 仅针对可恢复的网络/运行时错误重试
                # LLMNetworkError 已经耗尽 LLM 层的 10 次重试，重新跑主题会
                # 让用户再次等待且掩盖原始网络错误，因此直接终止该主题。
                is_recoverable = (
                    not isinstance(exc, LLMNetworkError)
                    and isinstance(exc, (OSError, TimeoutError, ConnectionError, RuntimeError))
                )
                if is_recoverable and topic.retry_count < topic.max_retries:
                    topic.retry_count += 1
                    topic.status = TopicStatus.QUEUED
                    should_retry = True
                    retry_delay = 0.05 * topic.retry_count
                    message = f"子主题《{topic.name}》异常，准备第 {topic.retry_count}/{topic.max_retries} 次重试：{exc}"
                else:
                    topic.status = TopicStatus.FAILED
                    topic.error = str(exc)
                    topic.finished_at = time.time()
                    message = f"子主题《{topic.name}》取证失败：{exc}"
                try:
                    self._persist_state_locked()
                except Exception:
                    pass
                self.emit(
                    "topic", message, topic_id=topic.id, topic_name=topic.name,
                    topic_status=topic.status.value,
                )

            # 退避等待必须在锁外进行，支持快速毫秒唤醒
            if should_retry and not topic._cancel_event.is_set():
                topic._cancel_event.wait(timeout=retry_delay)

    @staticmethod
    def _fulltext_index_ready(persist_dir: str | None) -> bool:
        """目录名不是就绪信号；只有可加载、含真实片段的索引可检索。"""
        if not persist_dir:
            return False
        store = load_vector_store(persist_dir)
        return store is not None and bool(store.documents)

    def _run_topic_pipeline(self, topic: SubtopicState, generation: int) -> None:
        """单个子主题的取证流水线，保证 tool_context 与 index 范围完全隔离。"""
        if topic._cancel_event.is_set():
            return

        settings = get_settings()

        if self.fake_mode:
            self._run_fake_topic(topic, generation)
            return

        scoped_persist_dir = str(
            get_session_scope_dir(settings["vector_store_dir"], f"{self.session_id}__{self.run_id}", f"selected_papers_{topic.id}")
        )

        def _topic_progress(msg: str) -> None:
            if topic._cancel_event.is_set() or topic.generation != generation or topic.status == TopicStatus.CANCELLED:
                return
            topic.latest_progress = msg
            self.emit("tool", msg, topic_id=topic.id, topic_name=topic.name)

        context = ToolContext(
            session_id=self.session_id,
            topic=topic.name,
            query=topic.name,
            top_k=self.top_k,
            corpus_persist_dir=scoped_persist_dir,
            fulltext_persist_dir=scoped_persist_dir,
            rebuild=True,
            progress=_topic_progress,
            topic_id=topic.id,
            cancel_event=topic._cancel_event,
        )

        if topic._cancel_event.is_set():
            return

        max_rounds = int(settings.get("max_research_rounds", 2) or 2)
        max_rounds = max(1, max_rounds)

        cumulative_papers: list[dict[str, Any]] = []
        cumulative_selected_papers: list[dict[str, Any]] = []
        cumulative_paper_notes: list[dict[str, Any]] = []
        cumulative_fulltext_notes: list[dict[str, Any]] = []
        cumulative_evidence_pool: list[dict[str, Any]] = []
        cumulative_evidence_status: list[dict[str, Any]] = []
        cumulative_tool_trace: list[dict[str, Any]] = []
        cumulative_citations: list[dict[str, Any]] = []
        evidence_gaps: list[str] = []
        next_queries: list[str] = []
        last_reflection: dict[str, Any] = {}
        last_loop_result: dict[str, Any] = {}
        downloaded: list[dict[str, Any]] = list(context.downloaded or [])

        # 2. 执行工具循环与多轮反思
        specs = get_toolset("topic_research")
        system_prompt = TOPIC_AGENT_SYSTEM

        for round_index in range(1, max_rounds + 1):
            if topic._cancel_event.is_set():
                return

            context.finished = False
            context.finish_reason = ""

            if round_index == 1:
                batch_queries = [
                    {"label": "broad", "query": topic.name},
                    {"label": "method", "query": f"{topic.name} methods"},
                ]
            else:
                if next_queries:
                    batch_queries = [{"label": f"gap_round_{round_index}", "query": q} for q in next_queries]
                elif evidence_gaps:
                    batch_queries = [{"label": f"gap_round_{round_index}", "query": f"{topic.name} {gap}"} for gap in evidence_gaps]
                else:
                    batch_queries = [{"label": f"gap_round_{round_index}", "query": f"{topic.name} comparison"}]

            goal_prompt = get_topic_agent_goal_prompt(
                topic.name,
                {"goal": f"调研 {topic.name}", "report_outline": list(DEFAULT_OUTLINE)},
                batch_queries,
                [],
                evidence_gaps,
                round_index,
            )

            loop_result = run_tool_loop(
                state={"progress_callback": _topic_progress},
                specs=specs,
                context=context,
                system_prompt=system_prompt,
                goal_prompt=goal_prompt,
                max_steps=DEFAULT_MAX_STEPS,
            )
            last_loop_result = loop_result
            if loop_result.get("trace"):
                cumulative_tool_trace.extend(loop_result["trace"])

            if topic._cancel_event.is_set() or loop_result.get("cancelled"):
                return

            # 3. 结果提炼与代表论文选择
            if loop_result.get("degraded") or not context.papers:
                search_bundle = search_academic_topic(
                    topic.name,
                    max_results=max(3, self.top_k),
                    query_batches=batch_queries if round_index > 1 else None,
                    progress_callback=_topic_progress,
                    cancel_event=topic._cancel_event,
                )
                round_papers = dedupe_papers(search_bundle.get("papers", []))
            else:
                round_papers = dedupe_papers(context.papers)
                if context.downloaded:
                    downloaded = list(context.downloaded)

            if topic._cancel_event.is_set():
                return

            if not round_papers and not cumulative_papers:
                # 没有候选论文时不让模型凭空反思/生成笔记，仍允许下一轮补搜。
                evidence_gaps = ["未检索到候选论文，缺少可核验的研究证据"]
                next_queries = [f"{topic.name} comparison"]
                last_reflection = {
                    "round": round_index, "sufficient": False,
                    "evidence_gaps": list(evidence_gaps), "next_queries": list(next_queries),
                    "stop_reason": "round_limit" if round_index >= max_rounds else "needs_evidence",
                }
                continue

            seen_arxiv = {str(p.get("arxiv_id", "") or "") for p in cumulative_papers if p.get("arxiv_id")}
            for p in round_papers:
                aid = str(p.get("arxiv_id", "") or "")
                if not aid or aid not in seen_arxiv:
                    cumulative_papers.append(p)
                    if aid:
                        seen_arxiv.add(aid)

            state_stub = {"progress_callback": _topic_progress}
            brief_notes = [
                self._extract_brief_note(topic.name, p, state_stub, cancel_event=topic._cancel_event)
                for p in round_papers[:6]
            ]
            cumulative_paper_notes.extend(brief_notes)

            round_selected = select_top_papers(topic.name, round_papers, limit=MAX_FULLTEXT_PAPERS_PER_TOPIC)
            if round_selected:
                seen_sel = {str(p.get("arxiv_id", "") or "") for p in cumulative_selected_papers if p.get("arxiv_id")}
                for p in round_selected:
                    aid = str(p.get("arxiv_id", "") or "")
                    if not aid or aid not in seen_sel:
                        cumulative_selected_papers.append(p)
                        if aid:
                            seen_sel.add(aid)

            if topic._cancel_event.is_set():
                return

            # 4. 全文检索与索引构建（下载共享，索引按 topic 强隔离）
            if round_selected:
                indexed_ids = {str(item.get("arxiv_id", "")) for item in downloaded}
                wanted_ids = {str(p.get("arxiv_id", "")) for p in round_selected}
                if not self._fulltext_index_ready(context.fulltext_persist_dir) or (wanted_ids - indexed_ids):
                    persist_dir, downloaded = build_selected_papers_index(
                        self.session_id,
                        round_selected,
                        rebuild=True,
                        progress_callback=_topic_progress,
                        topic_id=topic.id,
                        cancel_event=topic._cancel_event,
                        persist_dir=context.fulltext_persist_dir,
                        run_id=self.run_id,
                    )
                    context.fulltext_persist_dir = persist_dir

            if topic._cancel_event.is_set():
                return

            fulltext_docs: list[dict[str, Any]] = []
            if self._fulltext_index_ready(context.fulltext_persist_dir):
                query_text = next_queries[0] if (round_index > 1 and next_queries) else topic.name
                fulltext_docs = retrieve_documents(query_text, top_k=6, persist_dir=context.fulltext_persist_dir)

            # 5. 全文深读与笔记整理
            round_notes: list[dict[str, Any]] = []
            round_evidence_status: list[dict[str, Any]] = []

            for paper in round_selected:
                if topic._cancel_event.is_set():
                    return
                matched_docs, evidence_status_label = research_service.docs_for_paper(paper, fulltext_docs)
                p_docs = [copy.deepcopy(d) for d in matched_docs[:2]]

                # 必须在生成新笔记前先登记进累积证据池，去重并分配跨轮稳定的局部全局 [S1, S2...]
                if p_docs:
                    p_docs = register_evidence(cumulative_evidence_pool, p_docs)
                    note = self._deep_read_paper(topic.name, paper, p_docs, state_stub, cancel_event=topic._cancel_event)
                    round_notes.append(note)
                round_evidence_status.append(
                    {
                        "title": paper.get("title", ""),
                        "arxiv_id": paper.get("arxiv_id", ""),
                        "evidence_status": evidence_status_label,
                        "evidence_count": len(p_docs),
                    }
                )

            cumulative_fulltext_notes.extend(round_notes)
            cumulative_evidence_status.extend(round_evidence_status)
            cumulative_citations = build_citations(cumulative_evidence_pool)

            # 6. 反思步骤
            reflection = self._reflect_topic(
                topic.name,
                cumulative_fulltext_notes,
                cumulative_citations,
                state_stub,
                evidence_status=cumulative_evidence_status,
                round_index=round_index,
                max_rounds=max_rounds,
                cancel_event=topic._cancel_event,
            )
            last_reflection = reflection
            evidence_gaps = list(reflection.get("evidence_gaps") or [])
            next_queries = list(reflection.get("next_queries") or [])

            if topic._cancel_event.is_set():
                return

            is_sufficient = bool(reflection.get("sufficient", False))
            if is_sufficient or round_index >= max_rounds:
                break

            self.emit(
                "topic",
                f"子主题《{topic.name}》第 {round_index} 轮反思发现缺口，准备启动第 {round_index + 1} 轮补充取证：{evidence_gaps}",
                topic_id=topic.id,
                topic_name=topic.name,
            )

        # 7. 原子提交成果（代次检查防晚到写回）
        with self._lock:
            if topic.generation != generation or topic._cancel_event.is_set() or topic.status == TopicStatus.CANCELLED:
                return

            topic.research_rounds = round_index
            topic.papers = cumulative_papers
            topic.selected_papers = cumulative_selected_papers
            topic.paper_notes = cumulative_paper_notes
            topic.fulltext_notes = cumulative_fulltext_notes
            topic.evidence_pool = cumulative_evidence_pool
            topic.citations = cumulative_citations
            topic.evidence_status = cumulative_evidence_status
            topic.evidence_gaps = evidence_gaps
            topic.reflection = last_reflection
            topic.tool_trace = cumulative_tool_trace
            topic.tool_loop_degraded = bool(last_loop_result.get("degraded"))
            topic.vector_persist_dir = context.fulltext_persist_dir if cumulative_evidence_pool else None
            has_evidence = bool(cumulative_papers and cumulative_evidence_pool)
            topic.status = TopicStatus.COMPLETED if has_evidence else TopicStatus.FAILED
            topic.error = "" if has_evidence else (
                "未检索到候选论文，无法生成有证据的调研报告" if not cumulative_papers
                else "未获得可核验的全文证据，无法生成有证据的调研报告"
            )
            if not has_evidence:
                topic.evidence_gaps = list(dict.fromkeys([*evidence_gaps, topic.error]))
                topic.reflection = {**last_reflection, "sufficient": False, "evidence_gaps": topic.evidence_gaps}
            topic.finished_at = time.time()
            topic.latest_progress = (
                f"取证完成，共获得 {len(cumulative_evidence_pool)} 个全文证据片段"
                if has_evidence else topic.error
            )

            try:
                self._persist_state_locked()
            except Exception:
                pass

            self.emit(
                "topic",
                f"子主题《{topic.name}》取证完成" if has_evidence else f"子主题《{topic.name}》取证失败：{topic.error}",
                topic_id=topic.id,
                topic_name=topic.name,
                topic_status=topic.status.value,
            )

    def _run_metadata_topic_pipeline(self, topic: SubtopicState, generation: int) -> None:
        """Collect one bounded metadata candidate pool; no full text or embeddings.

        This is intentionally a separate worker stage.  Selection belongs to
        the parent run because the LLM must compare candidates across all
        subtopics rather than make an independent fixed-size choice per topic.
        """
        if topic._cancel_event.is_set():
            return
        def progress(message: str) -> None:
            if topic._cancel_event.is_set() or topic.generation != generation:
                return
            topic.latest_progress = message
            self.emit("search", message, topic_id=topic.id, topic_name=topic.name,
                      details={"stage": "metadata_search"})

        local_timings: dict[str, dict[str, Any]] = {}
        try:
            with stage_timing("query_generation", local_timings):
                query_batches = self._generate_metadata_query_batches(topic.name, topic._cancel_event)

            with stage_timing("metadata_search", local_timings):
                bundle = search_metadata_candidates(
                    topic.name,
                    query_batches=query_batches,
                    max_candidates=CANDIDATE_CAP_PER_SUBTOPIC,
                    progress_callback=progress,
                    cancel_event=topic._cancel_event,
                )
            candidates = normalize_and_dedupe_candidates(
                bundle.get("candidates", []),
                max_per_subtopic=CANDIDATE_CAP_PER_SUBTOPIC,
            )
            artifact = build_candidate_artifact(candidates, query=self.initial_query)
            settings = get_settings()
            artifact_dir = Path(settings["reports_dir"]) / f"{self.session_id}_{self.run_id}_candidates"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            json_path = artifact_dir / f"{topic.id}.json"
            markdown_path = artifact_dir / f"{topic.id}.md"
            json_path.write_text(candidate_artifact_to_json(artifact), encoding="utf-8")
            markdown_path.write_text(candidate_artifact_to_markdown(artifact), encoding="utf-8")
            local_timings["candidate_artifact"] = {"duration_seconds": 0.0, "duration_ms": 0.0}

            with self._lock:
                if topic.generation != generation or topic._cancel_event.is_set() or topic.status == TopicStatus.CANCELLED:
                    return
                topic.query_batches = copy.deepcopy(query_batches)
                topic.papers = copy.deepcopy(candidates)
                topic.selected_papers = []
                topic.paper_notes = []
                topic.fulltext_notes = []
                topic.evidence_pool = []
                topic.citations = []
                topic.evidence_status = []
                topic.evidence_gaps = [] if candidates else ["未检索到候选论文元数据"]
                topic.reflection = {"stage": "metadata_search", "candidate_count": len(candidates)}
                topic.candidate_artifact_path = str(json_path)
                topic.stage_timings = local_timings
                topic.research_rounds = 1
                topic.status = TopicStatus.COMPLETED if candidates else TopicStatus.FAILED
                topic.error = "" if candidates else "未检索到候选论文元数据"
                topic.finished_at = time.time()
                topic.latest_progress = f"候选元数据完成：{len(candidates)}/{CANDIDATE_CAP_PER_SUBTOPIC} 篇"
                self.candidate_artifact_paths[topic.id] = str(json_path)
                self._persist_state_locked()
            self.emit("topic", topic.latest_progress, topic_id=topic.id, topic_name=topic.name,
                      topic_status=topic.status.value,
                      details={"stage": "metadata_search", "items_completed": len(candidates), "items_total": CANDIDATE_CAP_PER_SUBTOPIC})
        except InterruptedError:
            raise
        except Exception:
            raise

    def _run_fake_topic(self, topic: SubtopicState, generation: int) -> None:
        """fake 模式快速运行，支持模拟故障与取消。"""
        fake_fail = bool((os.getenv("RESEARCH_AGENT_FAKE_FAIL", "") or "").strip().lower() in {"1", "true", "yes", "on"})
        if fake_fail or "__fail__" in topic.name or "__fail__" in self.initial_query:
            raise RuntimeError(f"模拟故障触发 (RESEARCH_AGENT_FAKE_FAIL): {topic.name}")

        delay = float(os.getenv("RESEARCH_AGENT_FAKE_DELAY", "0.01") or 0)
        stages = ["search", "select", "fulltext", "notes", "reflect"]
        for s in stages:
            if topic._cancel_event.is_set():
                return
            if delay:
                time.sleep(delay)
            self.emit("tool", f"[{s}] 处理中...", topic_id=topic.id, topic_name=topic.name)

        clean_id = topic.id.replace("topic-", "")
        fake_papers = [
            {
                "title": f"Paper on {topic.name}",
                "arxiv_id": f"2401.{clean_id}001",
                "summary": f"Discussion about {topic.name}.",
            }
        ]
        fake_docs = [
            {
                "page_content": f"Evidence snippet for {topic.name}",
                "metadata": {
                    "arxiv_id": fake_papers[0]["arxiv_id"],
                    "paper_title": fake_papers[0]["title"],
                    "source_name": f"{topic.id}.pdf",
                    "page": 1,
                    "chunk_index": 0,
                    "topic_id": topic.id,
                    "topic_name": topic.name,
                },
            }
        ]
        assign_citation_ids(fake_docs)

        with self._lock:
            if topic.generation != generation or topic._cancel_event.is_set() or topic.status == TopicStatus.CANCELLED:
                return
            topic.papers = fake_papers
            topic.selected_papers = fake_papers
            topic.paper_notes = [{"title": fake_papers[0]["title"], "summary": fake_papers[0]["summary"]}]
            topic.fulltext_notes = [{"title": fake_papers[0]["title"], "summary": f"精读笔记: {topic.name}"}]
            topic.evidence_pool = fake_docs
            topic.citations = build_citations(fake_docs)
            topic.status = TopicStatus.COMPLETED
            topic.finished_at = time.time()
            topic.latest_progress = "取证完成"
            try:
                self._persist_state_locked()
            except Exception:
                pass
            self.emit("topic", f"子主题《{topic.name}》完成", topic_id=topic.id, topic_status="completed")

    def _extract_brief_note(
        self,
        topic_name: str,
        paper: dict[str, Any],
        state: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        return research_service.brief_note(
            topic_name,
            paper,
            lambda prompt, **kwargs: invoke_llm(prompt, cancel_event=cancel_event, **kwargs),
        )

    def _deep_read_paper(
        self,
        topic_name: str,
        paper: dict[str, Any],
        docs: list[dict[str, Any]],
        state: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        return research_service.fulltext_note(
            topic_name,
            paper,
            docs,
            lambda prompt, **kwargs: invoke_llm(prompt, cancel_event=cancel_event, **kwargs),
        )

    def _reflect_topic(
        self, topic_name: str, fulltext_notes: list[dict[str, Any]], citations: list[dict[str, Any]],
        state: dict[str, Any], evidence_status: list[dict[str, Any]] | None = None,
        round_index: int = 1, max_rounds: int = 1,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        return research_service.review_evidence(
            topic_name, "topic_research", "\n".join(n.get("summary", "") for n in fulltext_notes),
            citations,
            evidence_status or [],
            [],
            round_index,
            max_rounds,
            lambda prompt, **kwargs: invoke_llm(prompt, cancel_event=cancel_event, **kwargs),
        )

    # -- 准入关闭与总报告撰写 ----------------------------------------------------

    def _check_intake_and_finalize_locked(self) -> None:
        """检查准入关闭条件：无排队主题、无进行中主题。"""
        if self.phase != RunPhase.COLLECTING:
            return

        has_active = any(t.status in {TopicStatus.QUEUED, TopicStatus.RUNNING} for t in self.topics)
        if not has_active and not self._active_workers:
            self._intake_closed = True
            self.phase = RunPhase.WRITING
            self.emit("scheduler", "所有子主题已无运行中任务，准入关闭，开始聚合撰写报告", phase="writing")
            try:
                self._persist_state_locked()
            except Exception:
                pass
            self._pool.submit(self._run_parent_aggregation)

    def _run_parent_aggregation(self) -> None:
        with retry_progress(lambda message: self.emit("llm", message)):
            self._aggregate_with_progress()

    def _fresh_llm_call(self, prompt: str, *, system: str, json_object: bool = False) -> str:
        """Call a role with a fresh single-turn context.

        The scheduler deliberately does not pass the tool-loop messages or a
        previous role's transcript into this helper.  That separation is part
        of the review/selection contract, not merely an implementation detail.
        """
        with retry_progress(lambda message: self.emit("llm", message)):
            return invoke_llm(
                prompt,
                system=system,
                json_object=json_object,
                cancel_event=self._cancel_event,
            )

    def _persist_candidate_artifact(self, candidates: list[dict[str, Any]], suffix: str = "all") -> str:
        settings = get_settings()
        target_dir = Path(settings["reports_dir"]) / f"{self.session_id}_{self.run_id}_candidates"
        target_dir.mkdir(parents=True, exist_ok=True)
        artifact = build_candidate_artifact(candidates, query=self.initial_query)
        json_path = target_dir / f"{suffix}.json"
        markdown_path = target_dir / f"{suffix}.md"
        json_path.write_text(candidate_artifact_to_json(artifact), encoding="utf-8")
        markdown_path.write_text(candidate_artifact_to_markdown(artifact), encoding="utf-8")
        return str(json_path)

    def _generate_metadata_query_batches(self, topic: str, cancel_event: Any) -> list[dict[str, Any]]:
        """Generate only English query batches for the metadata stage."""
        from agent.json_utils import parse_json_dicts
        from prompts import QUERY_BATCH_SYSTEM, get_query_batch_prompt

        raw = self._fresh_llm_call(
            get_query_batch_prompt(topic, self.search_plan or {"subtopics": [topic]}),
            system=QUERY_BATCH_SYSTEM,
        )
        batches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in parse_json_dicts(raw):
            query = " ".join(str(item.get("query", "")).split())
            if not query or any("\u3400" <= ch <= "\u9fff" for ch in query) or not any(ch.isascii() and ch.isalpha() for ch in query):
                continue
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            batches.append({"label": str(item.get("label") or query), "query": query})
            if len(batches) >= 8:
                break
        if not batches:
            raise ValueError("LLM did not produce an English arXiv query")
        return batches

    def _aggregate_metadata_pipeline(self) -> None:
        """Global select -> optional one-round supplement -> shared full text.

        Subtopic workers have already completed at this point.  This method is
        the only place that downloads/indexes papers in the new flow, ensuring
        that a paper selected by two subtopics is fetched and embedded once.
        """
        from agent.deep_research_pipeline import build_candidate_artifact
        from prompts import (
            REPORT_REVIEW_SYSTEM,
            RESEARCH_SYSTEM,
            SELECTOR_SYSTEM,
            get_candidate_selection_prompt,
            get_report_review_prompt,
        )

        with self._lock:
            completed_topics = [t for t in self.topics if t.status == TopicStatus.COMPLETED and t.papers]
            failed_topics = [t for t in self.topics if t.status == TopicStatus.FAILED]
            cancelled_topics = [t for t in self.topics if t.status == TopicStatus.CANCELLED]
        if not completed_topics:
            with self._lock:
                for topic in self.topics:
                    if topic.status == TopicStatus.COMPLETED and not topic.papers:
                        topic.status = TopicStatus.FAILED
                        topic.error = "未获得候选论文元数据"
                self.phase = RunPhase.FAILED
                self.error = "所有子主题均未获得候选论文元数据"
                self.emit("report", self.error, phase="failed")
                self._persist_state_locked()
            return

        stage_timings: dict[str, dict[str, Any]] = {}
        candidates: list[dict[str, Any]] = []
        for topic in completed_topics:
            candidates.extend(topic.papers)
        candidates = normalize_and_dedupe_candidates(candidates, max_per_subtopic=CANDIDATE_CAP_PER_SUBTOPIC)
        self._persist_candidate_artifact(candidates, "all")
        self.emit("candidates", f"已汇总 {len(candidates)} 篇候选论文元数据", phase="collecting",
                  details={"stage": "candidate_merge", "items_completed": len(candidates)})

        selection: dict[str, Any] = {}
        with stage_timing("candidate_selection", stage_timings):
            prompt = get_candidate_selection_prompt(
                self.initial_query,
                [t.name for t in completed_topics],
                candidates,
                [],
            )
            raw = self._fresh_llm_call(prompt, system=SELECTOR_SYSTEM, json_object=True)
            selection = parse_selector_response(raw, candidates)

        # One and only one supplementary metadata round.  It never downloads
        # papers and remains bounded by the per-subtopic candidate cap.
        additional_queries = list(selection.get("additional_english_queries") or [])
        if additional_queries:
            with stage_timing("supplement_search", stage_timings):
                self.emit("search", f"选择 LLM 发现 {len(additional_queries)} 个证据缺口，开始补充搜索", phase="collecting")
                for topic in completed_topics:
                    if self._cancel_event.is_set():
                        raise InterruptedError("补充搜索已取消")
                    existing_ids = {str(p.get("arxiv_id") or p.get("candidate_id") or "") for p in topic.papers}
                    remaining = max(0, CANDIDATE_CAP_PER_SUBTOPIC - len(topic.papers))
                    if remaining <= 0:
                        continue
                    batches = [{"label": "supplement", "query": query} for query in additional_queries]
                    bundle = search_metadata_candidates(
                        topic.name,
                        query_batches=batches,
                        max_candidates=remaining,
                        progress_callback=lambda msg, t=topic: self.emit("search", msg, topic_id=t.id, topic_name=t.name),
                        cancel_event=self._cancel_event,
                    )
                    additions = [p for p in bundle.get("candidates", []) if str(p.get("arxiv_id") or p.get("candidate_id") or "") not in existing_ids]
                    topic.papers = normalize_and_dedupe_candidates(
                        [*topic.papers, *additions],
                        max_per_subtopic=CANDIDATE_CAP_PER_SUBTOPIC,
                    )
                    topic.evidence_gaps = list(selection.get("evidence_gaps") or [])
                candidates = normalize_and_dedupe_candidates(
                    [p for t in completed_topics for p in t.papers],
                    max_per_subtopic=CANDIDATE_CAP_PER_SUBTOPIC,
                )
                self._persist_candidate_artifact(candidates, "all_supplemented")
            with stage_timing("candidate_selection_repeat", stage_timings):
                prompt = get_candidate_selection_prompt(
                    self.initial_query,
                    [t.name for t in completed_topics],
                    candidates,
                    selection.get("evidence_gaps") or [],
                )
                raw = self._fresh_llm_call(prompt, system=SELECTOR_SYSTEM, json_object=True)
                selection = parse_selector_response(raw, candidates)

        self.global_selection = selection
        selected = list(selection.get("selected_candidates") or [])[:GLOBAL_SELECTED_CAP]
        if not selected:
            with self._lock:
                self.phase = RunPhase.FAILED
                self.error = "选择 LLM 未选择任何可精读论文"
                self.emit("report", self.error, phase="failed")
                self._persist_state_locked()
            return
        self.emit("selection", f"选择 LLM 决定精读 {len(selected)} 篇论文（硬上限 {GLOBAL_SELECTED_CAP}）", phase="collecting",
                  details={"stage": "candidate_selection", "items_completed": len(selected), "items_total": GLOBAL_SELECTED_CAP})

        with stage_timing("fulltext_index", stage_timings):
            settings = get_settings()
            shared_dir = get_session_scope_dir(
                settings["vector_store_dir"],
                f"{self.session_id}__{self.run_id}",
                "selected_papers_shared",
            )
            persist_dir, downloaded = build_selected_papers_index(
                self.session_id,
                selected,
                rebuild=True,
                progress_callback=lambda msg: self.emit("fulltext", msg, phase="collecting"),
                cancel_event=self._cancel_event,
                persist_dir=shared_dir,
                run_id=self.run_id,
            )
        selected = downloaded[:GLOBAL_SELECTED_CAP]
        if not selected or not self._fulltext_index_ready(persist_dir):
            with self._lock:
                self.phase = RunPhase.FAILED
                self.error = "最终论文全文下载或共享索引构建失败"
                self.emit("report", self.error, phase="failed")
                self._persist_state_locked()
            return

        evidence_docs: list[dict[str, Any]] = []
        fulltext_notes: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        with stage_timing("deep_reading", stage_timings):
            for index, paper in enumerate(selected, 1):
                if self._cancel_event.is_set():
                    raise InterruptedError("精读已取消")
                docs = retrieve_documents(self.initial_query, top_k=8, persist_dir=persist_dir)
                matched, label = research_service.docs_for_paper(paper, docs)
                matched = [copy.deepcopy(d) for d in matched[:3]]
                matched = register_evidence(evidence_docs, matched)
                if matched:
                    fulltext_notes.append(self._deep_read_paper(self.initial_query, paper, matched, {}, cancel_event=self._cancel_event))
                evidence_status.append({
                    "title": paper.get("title", ""),
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "evidence_status": label,
                    "evidence_count": len(matched),
                })
                self.emit("deep_read", f"精读论文 {index}/{len(selected)}", phase="collecting",
                          details={"stage": "deep_reading", "items_completed": index, "items_total": len(selected)})
        assign_citation_ids(evidence_docs)
        citations = build_citations(evidence_docs)
        if not evidence_docs:
            with self._lock:
                self.phase = RunPhase.FAILED
                self.error = "最终论文未获得可核验的全文证据"
                self.emit("report", self.error, phase="failed")
                self._persist_state_locked()
            return

        with stage_timing("report_writing", stage_timings):
            outline = list(self.search_plan.get("report_outline") or DEFAULT_OUTLINE)
            notes = "\n\n".join(note.get("summary", "") for note in fulltext_notes)
            sections = self._write_sections(outline, notes, evidence_docs, query=self.initial_query)
            composed = self._compose_report(self.initial_query, sections)
            final_answer, audit = enforce_citations(composed, citations)

        with stage_timing("report_review", stage_timings):
            review_prompt = get_report_review_prompt(
                self.initial_query,
                [t.name for t in completed_topics],
                final_answer,
                fulltext_notes,
                citations,
            )
            review_raw = self._fresh_llm_call(review_prompt, system=REPORT_REVIEW_SYSTEM, json_object=True)
            review = parse_report_review_response(review_raw)
        self.report_review = review

        if not review.get("sufficient", False):
            with stage_timing("report_revision", stage_timings):
                revision_prompt = (
                    "任务标记：report_revision\n"
                    f"总主题：{self.initial_query}\n"
                    f"子主题：{[t.name for t in completed_topics]}\n"
                    f"评审意见：{review}\n"
                    f"最终论文笔记：{fulltext_notes}\n"
                    "请只修改以下报告，保留有证据支持的内容，补足评审指出的问题，不得编造论文未提供的事实。\n"
                    f"报告：\n{final_answer}"
                )
                revised = self._fresh_llm_call(revision_prompt, system=RESEARCH_SYSTEM).strip()
                if revised:
                    final_answer, audit = enforce_citations(revised, citations)
                    self.revision_applied = True

        report_path = self._persist_report_file(final_answer)
        survey_artifact = {
            "topic": self.initial_query,
            "outline": outline,
            "sections": sections,
            "full_text": final_answer,
            "papers": [p.get("title", "") for p in selected],
            "citations": citations,
            "citation_audit": audit,
            "candidate_artifact_paths": copy.deepcopy(self.candidate_artifact_paths),
            "selection": copy.deepcopy(selection),
            "review": copy.deepcopy(review),
            "revision_applied": self.revision_applied,
            "missing_topics": [t.name for t in failed_topics],
            "cancelled_topics": [t.name for t in cancelled_topics],
        }
        with self._lock:
            for topic in self.topics:
                topic.selected_papers = [p for p in selected if p.get("subtopic") == topic.name]
                topic.fulltext_notes = [n for n in fulltext_notes if n.get("arxiv_id") in {p.get("arxiv_id") for p in topic.selected_papers}]
                topic.evidence_pool = [d for d in evidence_docs if d.get("metadata", {}).get("topic_name") == topic.name]
                topic.citations = build_citations(topic.evidence_pool)
                topic.evidence_status = [e for e in evidence_status if e.get("arxiv_id") in {p.get("arxiv_id") for p in topic.selected_papers}]
                if topic.status == TopicStatus.COMPLETED:
                    topic.vector_persist_dir = str(persist_dir)
            self.stage_timings = stage_timings
            self.phase = RunPhase.SUCCEEDED
            self.final_state = {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "user_profile": copy.deepcopy(self.user_profile),
                "run_id": self.run_id,
                "query": self.initial_query,
                "task": self.initial_query,
                "topic": self.initial_query,
                "initial_query": self.initial_query,
                "intent_mode": "topic_research",
                "mode": "topic_research",
                "phase": "succeeded",
                "final_answer": final_answer,
                "final_report": final_answer,
                "survey_artifact": survey_artifact,
                "citations": citations,
                "citation_audit": audit,
                "selected_papers": selected,
                "paper_notes": fulltext_notes,
                "fulltext_notes": fulltext_notes,
                "evidence_status": evidence_status,
                "evidence_gaps": list(selection.get("evidence_gaps") or []),
                "research_rounds": 2 if additional_queries else 1,
                "stage_timings": copy.deepcopy(stage_timings),
                "candidate_artifact_paths": copy.deepcopy(self.candidate_artifact_paths),
                "selection": copy.deepcopy(selection),
                "report_review": copy.deepcopy(review),
                "revision_applied": self.revision_applied,
                "topics": [t.to_dict() for t in self.topics],
                "commands": [c.to_dict() for c in self.commands],
                "report_path": report_path,
            }
            self.final_state["shared_fulltext_index"] = str(persist_dir)
            self.final_state["research_context"] = build_research_context(self.final_state)
            self._persist_state_locked()
        self._update_parent_memory()
        self.emit("report", "调研报告生成、独立评审与引用核验完成", phase="succeeded",
                  details={"stage": "completed", "elapsed_seconds": sum(float(v.get("duration_seconds", 0)) for v in stage_timings.values())})

    def _aggregate_with_progress(self) -> None:
        """总报告聚合：将所有有效子主题成果进行去重、引用重编[S1..]并汇总。"""
        try:
            if self.pipeline_mode == "metadata" and not self.fake_mode:
                self._aggregate_metadata_pipeline()
                return
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    return
                # 旧 checkpoint 也可能把空结果存成 completed，不能据此写证据报告。
                if not self.fake_mode:
                    for topic in self.topics:
                        if topic.status == TopicStatus.COMPLETED and not topic.evidence_pool:
                            topic.status = TopicStatus.FAILED
                            topic.error = "未获得可核验的研究证据，无法生成调研报告"
                valid_topics = [t for t in self.topics if t.status == TopicStatus.COMPLETED]
                failed_topics = [t for t in self.topics if t.status == TopicStatus.FAILED]
                cancelled_topics = [t for t in self.topics if t.status == TopicStatus.CANCELLED]

            # 极端情况 1：全取消
            if not valid_topics and cancelled_topics and not failed_topics:
                with self._lock:
                    self.phase = RunPhase.CANCELLED
                    self.emit("scheduler", "所有主题均已被取消，调研任务终止", phase="cancelled")
                    self._persist_state_locked()
                return

            # 极端情况 2：全失败
            if not valid_topics:
                with self._lock:
                    self.phase = RunPhase.FAILED
                    err_summary = "; ".join(f"{t.name}: {t.error}" for t in failed_topics)
                    self.error = f"所有子主题均执行失败：{err_summary}"
                    self.emit("scheduler", self.error, phase="failed")
                    self._persist_state_locked()
                return

            self.emit("report", f"正在整合 {len(valid_topics)} 个有效主题的取证成果", phase="writing")

            # 1. 论文去重
            seen_arxiv: set[str] = set()
            unified_selected_papers: list[dict[str, Any]] = []
            for t in valid_topics:
                for p in t.selected_papers:
                    aid = str(p.get("arxiv_id", "") or "")
                    if aid and aid in seen_arxiv:
                        continue
                    if aid:
                        seen_arxiv.add(aid)
                    p_copy = dict(p)
                    p_copy["topic_id"] = t.id
                    p_copy["topic_name"] = t.name
                    unified_selected_papers.append(p_copy)

            # 2. 证据片段合并与统一重新标号
            active_topic_names = [t.name for t in valid_topics]
            if cancelled_topics and active_topic_names:
                if any("\u4e00" <= ch <= "\u9fff" for ch in self.initial_query):
                    active_query = "、".join(active_topic_names)
                else:
                    active_query = " and ".join(active_topic_names)
            else:
                active_query = self.initial_query

            topic_doc_map: list[tuple[str, str, dict[str, Any]]] = []
            unified_evidence_docs: list[dict[str, Any]] = []
            for t in valid_topics:
                for doc in t.evidence_pool:
                    doc_copy = copy.deepcopy(doc)
                    meta = dict(doc_copy.get("metadata", {}))
                    old_cid = str(meta.get("citation_id") or "")
                    meta["topic_id"] = t.id
                    meta["topic_name"] = t.name
                    doc_copy["metadata"] = meta
                    unified_evidence_docs.append(doc_copy)
                    topic_doc_map.append((t.id, old_cid, doc_copy))

            assign_citation_ids(unified_evidence_docs)
            unified_citations = build_citations(unified_evidence_docs)

            # 建立每主题的局部引用号 -> 全局引用号映射
            topic_cid_maps: dict[str, dict[str, str]] = {}
            for tid, old_cid, doc_copy in topic_doc_map:
                new_cid = str(doc_copy.get("metadata", {}).get("citation_id") or "")
                if tid and old_cid and new_cid:
                    topic_cid_maps.setdefault(tid, {})[old_cid] = new_cid

            def _remap_notes_text(text: str, cid_map: dict[str, str]) -> str:
                if not cid_map:
                    return text
                return re.sub(r"\[(S\d+)\]", lambda m: f"[{cid_map.get(m.group(1), m.group(1))}]", text)

            # 3. 笔记合并与全局引用重映射（不篡改各主题自身保存的原始笔记）
            notes_sections: list[str] = []
            for t in valid_topics:
                cid_map = topic_cid_maps.get(t.id, {})
                t_notes = [n.get("summary", "") for n in t.fulltext_notes if n.get("summary")]
                if not t_notes:
                    t_notes = [n.get("summary", "") for n in t.paper_notes if n.get("summary")]
                remapped_notes = [_remap_notes_text(n, cid_map) for n in t_notes]
                block = f"### 子主题：{t.name}\n" + ("\n\n".join(remapped_notes) if remapped_notes else "该子主题未提取到深读笔记。")
                notes_sections.append(block)
            combined_notes = "\n\n".join(notes_sections)

            # 4. 故障主题未覆盖说明
            missing_coverage = ""
            if failed_topics:
                missing_coverage = "\n\n## 调研覆盖与缺失说明\n\n> 提示：以下子主题在调研取证过程中发生故障未能完成覆盖：\n" + "\n".join(
                    f"- **{t.name}**：{t.error or '执行异常中断'}" for t in failed_topics
                )

            # 5. 正文生成
            if self.fake_mode:
                raw_report = (
                    f"# {active_query} 调研报告\n\n"
                    f"## 综合概述\n\n本调研整合了 {len(valid_topics)} 个维度的最新研究进展。\n\n"
                    + "\n\n".join(f"## {t.name}\n\n关于该主题的代表性方法研究 [S1]。" for t in valid_topics)
                )
                if missing_coverage:
                    raw_report += missing_coverage
                final_answer, audit = enforce_citations(raw_report, unified_citations)
                outline = [t.name for t in valid_topics]
                sections = [{"title": t.name, "body": f"关于 {t.name} 的分析"} for t in valid_topics]
            else:
                outline = [f"{t.name} 进展与对比" for t in valid_topics]
                if not outline:
                    outline = list(DEFAULT_OUTLINE)
                self.emit("report", f"生成章节大纲：{outline}", phase="writing")

                sections = self._write_sections(outline, combined_notes, unified_evidence_docs, query=active_query)
                composed = self._compose_report(active_query, sections)
                if missing_coverage:
                    composed += missing_coverage

                final_answer, audit = enforce_citations(composed, unified_citations)

            # 6. 锁内准备中间聚合状态（私有存储，不提前宣布成功，避免泄露未完成产物并杜绝读锁饥饿）
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    self.final_state = {}
                    self._draft_final_state = None
                    return
                self.phase = RunPhase.FINALIZING
                survey_artifact = {
                    "topic": active_query,
                    "outline": outline,
                    "sections": sections,
                    "full_text": final_answer,
                    "papers": [p.get("title", "") for p in unified_selected_papers],
                    "citations": unified_citations,
                    "citation_audit": audit,
                    "missing_topics": [t.name for t in failed_topics],
                    "cancelled_topics": [t.name for t in cancelled_topics],
                }
                draft_final_state = {
                    "session_id": self.session_id,
                    "user_id": self.user_id,
                    "user_profile": copy.deepcopy(self.user_profile),
                    "run_id": self.run_id,
                    "query": active_query,
                    "task": active_query,
                    "topic": active_query,
                    "initial_query": self.initial_query,
                    "intent_mode": "topic_research",
                    "mode": "topic_research",
                    "phase": "finalizing",
                    "final_answer": final_answer,
                    "final_report": final_answer,
                    "survey_artifact": survey_artifact,
                    "citations": unified_citations,
                    "citation_audit": audit,
                    "selected_papers": unified_selected_papers,
                    "research_rounds": max([getattr(t, "research_rounds", 1) for t in valid_topics] or [1]),
                    "evidence_gaps": [g for t in valid_topics for g in t.evidence_gaps],
                    "tool_trace": [tr for t in valid_topics for tr in t.tool_trace],
                    "tool_loop_degraded": any(t.tool_loop_degraded for t in valid_topics),
                    "evidence_status": [es for t in valid_topics for es in t.evidence_status],
                    "topics": [t.to_dict() for t in self.topics],
                    "commands": [c.to_dict() for c in self.commands],
                    "report_path": "",
                }
                draft_final_state["paper_notes"] = [
                    {key: (_remap_notes_text(value, topic_cid_maps.get(topic.id, {})) if isinstance(value, str)
                           else [topic_cid_maps.get(topic.id, {}).get(cid, cid) for cid in value] if key == "citation_ids" and isinstance(value, list)
                           else copy.deepcopy(value)) for key, value in note.items()}
                    for topic in valid_topics
                    for note in (topic.fulltext_notes or topic.paper_notes)
                ]
                draft_final_state["fulltext_notes"] = list(draft_final_state["paper_notes"])
                draft_final_state["fulltext_indexes"] = list(dict.fromkeys(
                    str(topic.vector_persist_dir)
                    for topic in valid_topics
                    if topic.vector_persist_dir
                ))
                draft_final_state["research_context"] = build_research_context(draft_final_state)
                if self.resume_journal:
                    draft_final_state["resume_journal"] = copy.deepcopy(self.resume_journal)
                self._draft_final_state = draft_final_state

            # 7. 锁外保存报告文件与论文笔记
            report_path = self._persist_report_file(final_answer)

            if not (self.phase == RunPhase.CANCELLED or self._cancel_event.is_set()):
                self._update_parent_memory()

            # 8. 锁内终态发布与代次/取消双重校验（必须等待所有落盘完成才宣告成功）
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    self.final_state = {}
                    self._draft_final_state = None
                    return

                self.phase = RunPhase.SUCCEEDED
                draft = getattr(self, "_draft_final_state", None) or draft_final_state
                draft["phase"] = "succeeded"
                draft["report_path"] = report_path
                self.final_state = draft
                self._draft_final_state = None
                self.emit("report", "调研报告生成与引用核验完成", phase="succeeded")
                self._persist_state_locked()

        except InterruptedError:
            with self._lock:
                self.phase = RunPhase.CANCELLED
                self.final_state = {}
                self._draft_final_state = None
            return
        except Exception as exc:
            logger.error(f"parent aggregation error: {exc}", exc_info=True)
            with self._lock:
                if self.phase != RunPhase.CANCELLED:
                    self.phase = RunPhase.FAILED
                    self.error = f"报告撰写失败: {exc}"
                    self.final_state = {}
                    self._draft_final_state = None
                    self.emit("report", self.error, phase="failed")
                    try:
                        self._persist_state_locked()
                    except Exception:
                        pass

    def _update_parent_memory(self) -> None:
        """保存总调研的论文笔记，不推断偏好或生成长期记忆候选。"""
        try:
            from agent.nodes import update_memory_node
            draft = getattr(self, "_draft_final_state", None) or self.final_state or {}
            state_to_update = dict(draft)
            state_to_update["user_id"] = self.user_id
            state_to_update["user_profile"] = self.user_profile
            state_to_update["paper_notes"] = [
                note
                for t in self.topics
                if t.status == TopicStatus.COMPLETED
                for note in (t.fulltext_notes or t.paper_notes)
            ]
            updated = update_memory_node(state_to_update)
            if updated.get("user_profile"):
                self.user_profile = updated["user_profile"]
            target = getattr(self, "_draft_final_state", None) if getattr(self, "_draft_final_state", None) is not None else self.final_state
            if target is not None:
                if "pending_memory_candidates" in updated:
                    target["pending_memory_candidates"] = updated["pending_memory_candidates"]
                if "awaiting_memory_confirmation" in updated:
                    target["awaiting_memory_confirmation"] = updated["awaiting_memory_confirmation"]
                if "memory_summary" in updated:
                    target["memory_summary"] = updated["memory_summary"]
        except Exception as exc:
            logger.warning(f"Memory update in parent aggregation skipped or failed: {exc}")

    def _write_sections(
        self, outline: list[str], notes: str, docs: list[dict[str, Any]], query: str = ""
    ) -> list[dict[str, str]]:
        def before_section(index: int, title: str) -> None:
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    raise InterruptedError("报告撰写已取消")
            self.emit("report", f"正在撰写第 {index}/{len(outline)} 节：{title}", phase="writing")
        return research_service.write_sections(
            query or self.initial_query, outline, notes, docs,
            {"report_outline": outline, "user_profile": self.user_profile},
            lambda prompt, **kwargs: invoke_llm(prompt, cancel_event=self._cancel_event, **kwargs),
            before_section,
        )

    def _compose_report(self, title: str, sections: list[dict[str, str]]) -> str:
        return research_service.compose_report(title, sections)

    def _persist_report_file(self, report: str) -> str:
        if not report.strip():
            return ""
        with self._lock:
            if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                return ""
        settings = get_settings()
        reports_dir = Path(settings["reports_dir"])
        reports_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", self.initial_query).strip("_")[:40] or "report"
        final_path = reports_dir / f"{self.session_id}_{self.run_id}_{slug}.md"
        staged_path = reports_dir / f"{self.session_id}_{self.run_id}_{slug}.tmp_{uuid.uuid4().hex[:8]}.md"
        try:
            staged_path.write_text(report, encoding="utf-8")
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    try:
                        if staged_path.exists():
                            staged_path.unlink()
                    except OSError:
                        pass
                    return ""
                staged_path.replace(final_path)
            return str(final_path)
        except Exception as exc:
            try:
                if staged_path.exists():
                    staged_path.unlink()
            except OSError:
                pass
            with self._lock:
                if self.phase == RunPhase.CANCELLED or self._cancel_event.is_set():
                    return ""
            logger.error(f"Failed to persist report file: {exc}")
            raise

    # -- 外部状态查询与中断恢复 ------------------------------------------------

    def get_state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            current_phase = self.phase.value if isinstance(self.phase, RunPhase) else str(self.phase)
            snap: dict[str, Any] = {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "user_profile": copy.deepcopy(self.user_profile),
                "top_k": self.top_k,
                "run_id": self.run_id,
                "initial_query": self.initial_query,
                "query": self.initial_query,
                "task": self.initial_query,
                "topic": self.initial_query,
                "intent_mode": "topic_research",
                "phase": current_phase,
                "error": self.error,
                "pipeline_mode": self.pipeline_mode,
                "stage_timings": copy.deepcopy(self.stage_timings),
                "candidate_artifact_paths": copy.deepcopy(self.candidate_artifact_paths),
                "selection": copy.deepcopy(self.global_selection),
                "report_review": copy.deepcopy(self.report_review),
                "topics": [t.to_dict() for t in self.topics],
                "commands": [c.to_dict() for c in self.commands],
            }
            if self.resume_journal:
                snap["resume_journal"] = copy.deepcopy(self.resume_journal)
            if self.final_state:
                snap.update(self.final_state)
            snap["phase"] = current_phase
            return snap

    def resume_interrupted(self) -> None:
        """续跑中断的调研：保留已完成主题，仅恢复被中断的排队/运行主题。"""
        self._load_output_preferences()
        with self._lock:
            if self.phase not in {RunPhase.INTERRUPTED, RunPhase.COLLECTING, RunPhase.WRITING}:
                return

            has_uncompleted = any(
                t.status in {TopicStatus.INTERRUPTED, TopicStatus.RUNNING, TopicStatus.QUEUED}
                for t in self.topics
            )
            # 若所有主题此前均已完成，直接结转撰写报告，不重复运行
            if not has_uncompleted:
                self._intake_closed = True
                self.phase = RunPhase.WRITING
                self.emit("scheduler", "所有子主题此前均已完成取证，直接进入报告撰写阶段", phase="writing")
                try:
                    self._persist_state_locked()
                except Exception:
                    pass
                self._pool.submit(self._run_parent_aggregation)
                return

            self.phase = RunPhase.COLLECTING
            self._intake_closed = False
            for t in self.topics:
                if t.status in {TopicStatus.INTERRUPTED, TopicStatus.RUNNING}:
                    t.status = TopicStatus.QUEUED
                    t._cancel_event.clear()
                    t.generation += 1

            try:
                self._persist_state_locked()
            except Exception:
                pass
            self.emit("scheduler", "已恢复调研，排队未完成主题", phase="collecting")
            self._schedule_next_locked()
            self._check_intake_and_finalize_locked()

    def wait_until_complete(self, timeout: float | None = None) -> dict[str, Any]:
        """同步等待全任务结束。"""
        start_t = time.time()
        while True:
            with self._lock:
                if self.phase in {RunPhase.SUCCEEDED, RunPhase.CANCELLED, RunPhase.FAILED}:
                    return self.final_state or self.get_state_snapshot()
            if timeout and time.time() - start_t > timeout:
                raise TimeoutError(f"TopicScheduler timed out after {timeout}s")
            time.sleep(0.02)

    def shutdown(self) -> None:
        """安全关闭线程池，保护单调终态。"""
        with self._lock:
            if self.phase not in {RunPhase.SUCCEEDED, RunPhase.FAILED, RunPhase.CANCELLED}:
                self.cancel_run("调度器关闭")
        self._pool.shutdown(wait=False, cancel_futures=True)
