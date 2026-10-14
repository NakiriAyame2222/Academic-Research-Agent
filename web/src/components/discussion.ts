import type { ResearchResult } from "../types";

export const FOLLOW_UP_DISCUSSION_SOURCES = [
  "当前研究报告",
  "已选论文笔记",
  "按需取证",
] as const;

export interface DiscussionIndicator {
  scope?: string;
  sources: string[];
}

const SOURCE_LABELS: Record<string, string> = {
  report: "研究报告",
  research_report: "研究报告",
  selected_paper_notes: "已选论文笔记",
  paper_notes: "论文笔记",
  notes: "论文笔记",
  on_demand_evidence: "按需证据",
  ondemand_evidence: "按需证据",
  evidence: "证据",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function sourceLabel(value: unknown): string | undefined {
  const text = asString(value);
  if (!text) return undefined;
  const key = text.toLowerCase().replace(/[\s-]+/g, "_");
  return SOURCE_LABELS[key] || text;
}

function addSources(target: string[], value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach((item) => addSources(target, item));
    return;
  }
  const record = asRecord(value);
  if (record) {
    Object.entries(record).forEach(([key, enabled]) => {
      if (enabled) addSources(target, key);
    });
    return;
  }
  const text = asString(value);
  if (!text) return;
  text.split(/[,，|]/).forEach((item) => {
    const label = sourceLabel(item);
    if (label && !target.includes(label)) target.push(label);
  });
}

function readMetadata(result: ResearchResult): Record<string, unknown>[] {
  const candidates = [
    result.discussion,
    result.discussion_context,
    result.discussion_metadata,
    result.metadata,
  ];
  return candidates
    .map(asRecord)
    .filter((value): value is Record<string, unknown> => value !== null);
}

/** Extract optional scope/source metadata without changing behavior for old results. */
export function getDiscussionIndicator(result: ResearchResult): DiscussionIndicator | null {
  const metadata = readMetadata(result);
  const sources: string[] = [];

  metadata.forEach((item) => {
    addSources(sources, item.sources ?? item.source ?? item.source_labels ?? item.used_sources);
    if (item.report_used || item.uses_report) addSources(sources, "report");
    if (item.selected_paper_notes_used || item.paper_notes_used || item.uses_selected_paper_notes) {
      addSources(sources, "selected_paper_notes");
    }
    if (item.on_demand_evidence_used || item.ondemand_evidence_used || item.uses_on_demand_evidence) {
      addSources(sources, "on_demand_evidence");
    }
  });
  addSources(sources, result.discussion_sources);

  const scope = [
    result.discussion_scope,
    ...metadata.map((item) => item.scope ?? item.discussion_scope),
  ]
    .map(asString)
    .find(Boolean);

  return scope || sources.length > 0 ? { scope, sources } : null;
}
