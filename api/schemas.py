from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResearchRequest(BaseModel):
    query: str = ""
    file_path: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    rebuild_index: bool = False
    resume: bool = False
    explicit_resume: bool = False
    command: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str


class SubmitResponse(BaseModel):
    job_id: str
    session_id: str


class Citation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    source: str = ""
    source_name: str = ""
    paper_title: str = ""
    arxiv_id: str = ""
    page: int | None = None
    chunk_index: int = 0
    section_title: str = ""
    excerpt: str = ""  # 已截断到 200 字符


class CitationAudit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_references: int = 0
    distinct_references: int = 0
    available_citations: int = 0
    invalid_references: list[str] = []
    valid_reference_rate: float = 0.0
    citation_coverage: float = 0.0
    has_sources_section: bool = False
    sources_section_appended: bool = False


class ToolTraceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    step: int = 0
    tool: str = ""
    arguments: dict[str, Any] = {}
    result_summary: str = ""
    elapsed_seconds: float = 0.0


class EvidenceStatusItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    arxiv_id: str = ""
    evidence_status: str = ""  # "arxiv_id" | "source_name_prefix" | "paper_title" | "fallback"
    evidence_count: int = 0


class SubtopicView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    name: str = ""
    status: str = "queued"
    error: str = ""
    retry_count: int = 0
    latest_progress: str = ""
    started_at: float | None = None
    finished_at: float | None = None


class CommandRequest(BaseModel):
    command_id: str | None = None
    command: str | None = None
    raw_text: str | None = None
    topic: str | None = None
    topic_id: str | None = None


class CommandResponse(BaseModel):
    command_id: str = ""
    status: str = "accepted"  # accepted | rejected | clarification_needed | ignored
    message: str = ""
    topic_id: str | None = None
    topic_name: str | None = None
    phase: str = ""
    clarification_options: list[dict[str, str]] = []
    job_id: str | None = None


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int = 0
    stage: str = ""
    message: str = ""
    timestamp: str = ""
    topic_id: str | None = None
    topic_name: str | None = None
    topic_status: str | None = None
    command_id: str | None = None
    phase: str | None = None
    details: dict[str, Any] | None = None
    options: list[dict[str, str]] | None = None


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str = ""
    intent_mode: str = ""
    discussion_intent: str = ""
    discussion: dict[str, Any] | None = None
    discussion_scope: str | None = None
    discussion_sources: list[str] = []
    final_answer: str = ""
    survey_artifact: dict[str, Any] = {}
    citations: list[Citation] = []
    citation_audit: CitationAudit | None = None
    tool_trace: list[ToolTraceItem] = []
    tool_loop_degraded: bool = False
    evidence_status: list[EvidenceStatusItem] = []
    evidence_gaps: list[str] = []
    open_questions: list[str] = []
    research_rounds: int = 0
    selected_papers: list[dict[str, Any]] = []
    paper_notes: list[dict[str, Any]] = []
    pending_memory_candidates: list[dict[str, Any]] = []
    report_download_url: str | None = None


class JobView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: str
    session_id: str
    status: str
    phase: str = ""
    topics: list[SubtopicView] = []
    commands: list[dict[str, Any]] = []
    events: list[ProgressEvent] = []
    result: ResearchResult | None = None
    error: str = ""


class CurrentResearchView(BaseModel):
    session_id: str
    job_id: str | None = None
    is_active: bool = False
    phase: str = ""
    can_resume: bool = False
    topics: list[SubtopicView] = []


class HealthResponse(BaseModel):
    llm_available: bool = False
    tool_calling: bool = False
    vector_backend: str = ""
    jieba: bool = False
    degrade_notices: list[str] = []


class SessionSummary(BaseModel):
    session_id: str
    topic: str = ""
    has_report: bool = False
    updated_at: str = ""
    messages_count: int = 0


class SessionDetail(BaseModel):
    session_id: str
    updated_at: str = ""
    result: ResearchResult | None = None


class PaperInfo(BaseModel):
    file_name: str
    file_path: str
    size_bytes: int = 0
    suffix: str = ""


class MemorySaveRequest(BaseModel):
    indexes: list[int] = []


class MemorySaveResponse(BaseModel):
    saved: int = 0


class ResearchNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
