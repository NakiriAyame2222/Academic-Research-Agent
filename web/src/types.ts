// 与 api/schemas.py 对齐的前端类型。

export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export type TopicStatus = "queued" | "running" | "completed" | "cancelled" | "failed" | "interrupted";

export type RunPhase =
  | "initializing"
  | "collecting"
  | "writing"
  | "finalizing"
  | "succeeded"
  | "cancelled"
  | "failed"
  | "interrupted";

export interface SubtopicView {
  id: string;
  name: string;
  status: TopicStatus;
  error?: string;
  retry_count?: number;
  latest_progress?: string;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface CommandResponse {
  command_id: string;
  status: "accepted" | "rejected" | "clarification_needed" | "ignored";
  message: string;
  topic_id?: string;
  topic_name?: string;
  phase?: string;
  clarification_options?: Array<{ id: string; name: string }>;
  job_id?: string;
}

/** 可选的阶段进度元数据；额外字段由后端按需提供。 */
export interface ProgressEventDetails {
  stage?: string;
  elapsed_seconds?: number;
  items_completed?: number;
  items_total?: number;
  [key: string]: unknown;
}

export interface ProgressEvent {
  id: number;
  stage: string;
  message: string;
  timestamp: string;
  topic_id?: string;
  topic_name?: string;
  topic_status?: TopicStatus;
  command_id?: string;
  phase?: RunPhase;
  details?: ProgressEventDetails | null;
  options?: Array<{ id: string; name: string }>;
}

export interface Citation {
  id: string;
  source: string;
  source_name: string;
  paper_title: string;
  arxiv_id: string;
  page: number | null;
  chunk_index: number;
  section_title: string;
  excerpt: string;
}

export interface CitationAudit {
  total_references: number;
  distinct_references: number;
  available_citations: number;
  invalid_references: string[];
  valid_reference_rate: number;
  citation_coverage: number;
  has_sources_section: boolean;
  sources_section_appended?: boolean;
}

/**
 * Optional provenance attached to a report discussion/follow-up result.
 * The API may omit this metadata for older results, so all fields are optional.
 */
export interface DiscussionMetadata {
  scope?: string | null;
  source?: string | string[] | null;
  sources?: string[] | null;
  report_used?: boolean;
  selected_paper_notes_used?: boolean;
  on_demand_evidence_used?: boolean;
  [key: string]: unknown;
}

export interface ToolTraceItem {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  result_summary: string;
  elapsed_seconds: number;
}

export interface EvidenceStatusItem {
  title: string;
  arxiv_id: string;
  evidence_status: string;
  evidence_count: number;
}

export interface ResearchResult {
  session_id: string;
  intent_mode: string;
  final_answer: string;
  survey_artifact: Record<string, unknown>;
  citations: Citation[];
  citation_audit: CitationAudit | null;
  tool_trace: ToolTraceItem[];
  tool_loop_degraded: boolean;
  evidence_status: EvidenceStatusItem[];
  evidence_gaps: string[];
  open_questions: string[];
  research_rounds: number;
  selected_papers: Record<string, unknown>[];
  paper_notes: Record<string, unknown>[];
  pending_memory_candidates: Record<string, unknown>[];
  report_download_url: string | null;
  discussion?: DiscussionMetadata | null;
  discussion_context?: DiscussionMetadata | null;
  discussion_metadata?: DiscussionMetadata | null;
  discussion_scope?: string | null;
  discussion_sources?: string[] | null;
  metadata?: Record<string, unknown> | null;
}

export interface JobView {
  job_id: string;
  session_id: string;
  status: JobStatus;
  phase?: RunPhase;
  topics?: SubtopicView[];
  commands?: Array<Record<string, unknown>>;
  events: ProgressEvent[];
  result: ResearchResult | null;
  error: string;
}

export interface CurrentResearchView {
  session_id: string;
  job_id: string | null;
  is_active: boolean;
  phase: string;
  can_resume: boolean;
  topics: SubtopicView[];
}

export interface SessionSummary {
  session_id: string;
  topic: string;
  has_report: boolean;
  updated_at: string;
  messages_count: number;
}

export interface SessionDetail {
  session_id: string;
  updated_at: string;
  result: ResearchResult | null;
}

export interface PaperInfo {
  file_name: string;
  file_path: string;
  size_bytes: number;
  suffix: string;
}

export interface PendingMemory {
  index: number;
  content: string;
  memory_type: string;
  source_ref: string;
}
