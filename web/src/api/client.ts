import type {
  CommandResponse,
  CurrentResearchView,
  JobView,
  ResearchResult,
  SessionSummary,
} from "../types";

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as T;
}

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as T;
}

export function createSession(): Promise<{ session_id: string }> {
  return postJson("/api/sessions");
}

export function listSessions(): Promise<SessionSummary[]> {
  return getJson("/api/sessions");
}


export async function deleteSession(sessionId: string): Promise<void> {
  const resp = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
}


export async function uploadPaper(file: File): Promise<{ file_path: string }> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/papers/upload", { method: "POST", body: form });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as { file_path: string };
}

export interface SelectedFile {
  path: string;
  name: string;
}

export interface PaperInfo {
  file_name: string;
  file_path: string;
  size_bytes: number;
  suffix: string;
}

export function listPapers(): Promise<PaperInfo[]> {
  return getJson("/api/papers");
}

export interface RetrievalOptions {
  top_k?: number;
  rebuild_index: boolean;
}

export async function cancelJob(jobId: string): Promise<void> {
  const resp = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
}

export interface ConversationTurn {
  index: number;
  query: string;
  timestamp: string;
  result: ResearchResult;
}

export function getConversation(
  sessionId: string,
): Promise<{ session_id: string; turns: ConversationTurn[]; selected_file: SelectedFile | null }> {
  return getJson(`/api/sessions/${sessionId}/conversation`);
}

export interface ModelSettings {
  llm_model: string;
  llm_base_url: string;
  llm_api_key: string;
  embedding_model: string;
  embedding_base_url: string;
  embedding_api_key: string;
  max_research_rounds: number;
  default_top_k: number;
}

export function getSettings(): Promise<ModelSettings> {
  return getJson("/api/settings");
}

export function updateSettings(settings: ModelSettings): Promise<ModelSettings> {
  return postJson("/api/settings", settings);
}

export interface OutputPreferences {
  language: "Chinese" | "English";
  format: "structured" | "table" | "markdown" | "bullet" | "narrative";
  tone: "academic" | "concise" | "neutral" | "tutorial";
  depth: "overview" | "standard" | "deep";
}

export function getOutputPreferences(): Promise<OutputPreferences> {
  return getJson("/api/settings/preferences");
}

export function saveOutputPreferences(preferences: OutputPreferences): Promise<OutputPreferences> {
  return postJson("/api/settings/preferences", preferences);
}

export function saveResearchNote(sessionId: string, content: string): Promise<{ saved: number }> {
  return postJson(`/api/sessions/${sessionId}/notes`, { content });
}

export interface ResearchBody {
  query: string;
  file_path?: string;
  top_k?: number;
  rebuild_index?: boolean;
  resume?: boolean;
}

export function submitResearch(
  sessionId: string,
  body: ResearchBody,
): Promise<{ job_id: string; session_id: string }> {
  return postJson(`/api/sessions/${sessionId}/research`, body);
}

export async function getJob(jobId: string): Promise<JobView> {
  const resp = await fetch(`/api/jobs/${jobId}`);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as JobView;
}

export interface ResearchCommandPayload {
  command?: string;
  command_id?: string;
  raw_text?: string;
  topic?: string;
  topic_id?: string;
}

export function sendResearchCommand(
  sessionId: string,
  payload: ResearchCommandPayload,
): Promise<CommandResponse> {
  return postJson(`/api/sessions/${sessionId}/research/commands`, payload);
}

export function cancelTopic(sessionId: string, topicId: string): Promise<CommandResponse> {
  return postJson(`/api/sessions/${sessionId}/research/commands`, {
    command: "cancel_topic",
    topic_id: topicId,
  });
}

export function resumeResearch(sessionId: string): Promise<CommandResponse> {
  return postJson(`/api/sessions/${sessionId}/research/commands`, {
    command: "resume",
  });
}

export function getCurrentResearch(sessionId: string): Promise<CurrentResearchView> {
  return getJson(`/api/sessions/${sessionId}/research/current`);
}
