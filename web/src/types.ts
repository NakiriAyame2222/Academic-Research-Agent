// 与 api/schemas.py 对齐的前端类型。

export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface ProgressEvent {
  id: number;
  stage: string;
  message: string;
  timestamp: string;
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
}

export interface JobView {
  job_id: string;
  session_id: string;
  status: JobStatus;
  events: ProgressEvent[];
  result: ResearchResult | null;
  error: string;
}

export interface HealthInfo {
  llm_available: boolean;
  tool_calling: boolean;
  vector_backend: string;
  jieba: boolean;
  degrade_notices: string[];
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
