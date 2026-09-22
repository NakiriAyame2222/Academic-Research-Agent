import type { JobView, PendingMemory, ResearchResult, SessionSummary } from "../types";

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

export function listPendingMemories(sessionId: string): Promise<{ candidates: PendingMemory[] }> {
  return getJson(`/api/sessions/${sessionId}/memories`);
}

export function saveMemories(
  sessionId: string,
  indexes: number[],
): Promise<{ saved: number }> {
  return postJson(`/api/sessions/${sessionId}/memories`, { indexes });
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
): Promise<{ session_id: string; turns: ConversationTurn[] }> {
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
