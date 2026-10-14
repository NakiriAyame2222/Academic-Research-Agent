
import type {
  JobStatus,
  ProgressEvent,
  ProgressEventDetails,
  SubtopicView,
  TopicStatus,
} from "../types";

const STATUS_STYLE: Record<JobStatus, string> = {
  pending: "bg-paper text-ink-muted border border-rule",
  running: "bg-highlight/10 text-highlight border border-highlight/20",
  succeeded: "bg-accent-soft text-accent border border-accent/20",
  failed: "bg-red-50 text-danger border border-danger/20",
  cancelled: "bg-paper text-ink-muted border border-rule",
};

const STATUS_LABEL: Record<JobStatus, string> = {
  pending: "等待中",
  running: "研究中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const TOPIC_STYLE: Record<TopicStatus, string> = {
  queued: "bg-paper text-ink-muted border-rule",
  running: "bg-highlight/10 text-highlight border-highlight/30",
  completed: "bg-accent-soft text-accent border-accent/30",
  failed: "bg-red-50 text-danger border-danger/30",
  cancelled: "bg-paper text-ink-muted border-rule line-through",
  interrupted: "bg-yellow-50 text-yellow-700 border-yellow-200",
};

const TOPIC_LABEL: Record<TopicStatus, string> = {
  queued: "排队中",
  running: "取证中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const URL_PATTERN = /\b(?:https?:\/\/|www\.)[^\s<>"')]+/gi;

/**
 * Progress events are intentionally kept verbatim in the API/job state. This
 * formatter only controls what is shown in the compact timeline, so URLs,
 * queries, and implementation errors do not make the page wider than its
 * container.
 */
export function formatProgressMessage(message: string): string {
  const text = message.replace(/\s+/g, " ").trim();
  if (!text) return "";

  // 终态失败不能压缩成“正在重试”，否则会误导用户。
  if (/LLM.*已重试.*仍失败/i.test(text)) return "LLM 网络错误，重试耗尽，请检查网络后重新执行";
  if (/达到.*次尝试|跳过该查询|Atom.*错误|无法解析/i.test(text) && /arxiv/i.test(text)) {
    return "arXiv 搜索失败";
  }
  if (/不支持.*tools|端点不支持.*tool calling|未启用原生.*tool calling/i.test(text)) {
    return "使用备用工具调用流程";
  }

  const isArxiv = /arxiv|search_arxiv/i.test(text);
  const isUnavailable = /(?:请求失败|搜索异常|返回\s+http|返回内容无法解析|atom\s+错误|搜索失败|不可用|超时|failed|error)/i.test(
    text,
  );

  if (isArxiv && isUnavailable) {
    return "arXiv 暂时不可用，正在重试";
  }

  if (isArxiv && /重定向到|开始\s+arXiv\s+搜索|search_arxiv|arXiv\s+搜索/i.test(text)) {
    const hitCount = text.match(/命中\s*(\d+)\s*篇/);
    return hitCount ? `搜索 arXiv 完成 · 命中 ${hitCount[1]} 篇` : "搜索 arXiv";
  }

  if (
    /\bLLM\b|tool calling/i.test(text) &&
    /(?:失败|不支持|降级|不可用|异常|超时|failed|error)/i.test(text)
  ) {
    return "LLM 暂时不可用，正在重试";
  }

  const localSearchHit = text.match(/(?:全文证据检索完成|检索完成)[，,]\s*命中\s*(\d+)\s*个片段/);
  if (localSearchHit) {
    return `搜索 paper 完成 · 命中 ${localSearchHit[1]} 个片段`;
  }

  if (/search_(?:local|papers)|本地论文证据|全文索引检索/i.test(text)) {
    return "搜索 paper";
  }

  // Tool arguments and redirected URLs are useful for diagnostics but not as
  // progress copy. Keep a short action label instead of rendering the payload.
  if (URL_PATTERN.test(text) || /(?:^|[([{\s])(?:query|url)\s*[:=]/i.test(text)) {
    URL_PATTERN.lastIndex = 0;
    return "处理链接";
  }
  URL_PATTERN.lastIndex = 0;

  if (
    text.length > 100 &&
    /(?:内部错误|网络|连接|connection|timeout|timed out|traceback|exception)/i.test(text)
  ) {
    return "服务暂时不可用，正在重试";
  }

  return text.length > 140 ? `${text.slice(0, 140)}…` : text;
}

function asFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatElapsedSeconds(value: number): string {
  const seconds = Math.max(0, value);
  if (seconds < 60) {
    const rounded = Math.round(seconds * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} 秒`;
  }

  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round((seconds - minutes * 60) * 10) / 10;
  if (remaining === 0) return `${minutes} 分钟`;
  return `${minutes} 分 ${Number.isInteger(remaining) ? remaining : remaining.toFixed(1)} 秒`;
}

/** 将阶段详情压缩成不会撑宽时间线的中文摘要。 */
export function formatProgressDetails(details?: ProgressEventDetails | null): string {
  if (!details) return "";

  const parts: string[] = [];
  const stage = typeof details.stage === "string" ? details.stage.replace(/\s+/g, " ").trim() : "";
  if (stage) parts.push(`阶段 ${stage.slice(0, 48)}${stage.length > 48 ? "…" : ""}`);

  const completed = asFiniteNumber(details.items_completed);
  const total = asFiniteNumber(details.items_total);
  if (completed !== null && total !== null) {
    parts.push(`进度 ${formatCount(completed)}/${formatCount(total)} 项`);
  } else if (completed !== null) {
    parts.push(`已完成 ${formatCount(completed)} 项`);
  } else if (total !== null) {
    parts.push(`共 ${formatCount(total)} 项`);
  }

  const elapsed = asFiniteNumber(details.elapsed_seconds);
  if (elapsed !== null) parts.push(`耗时 ${formatElapsedSeconds(elapsed)}`);

  return parts.join(" · ");
}

interface ProgressLogProps {
  events: ProgressEvent[];
  status: JobStatus;
  phase?: string;
  topics?: SubtopicView[];
  clarificationOptions?: Array<{ id: string; name: string }>;
  error: string;
  onCancelTopic?: (topicId: string) => void;
}

export function ProgressLog({
  events,
  status,
  phase,
  topics = [],
  clarificationOptions = [],
  error,
  onCancelTopic,
}: ProgressLogProps) {
  const isCollecting = phase === "collecting" || (!phase && status === "running");

  return (
    <section className="min-w-0 space-y-4 rounded-lg border border-rule bg-surface p-4">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <h2 className="font-sans text-sm font-medium text-ink">推理时间线</h2>
          {phase && (
            <span className="min-w-0 truncate rounded bg-paper px-2 py-0.5 font-mono text-[11px] text-ink-muted" title={phase}>
              阶段: {phase}
            </span>
          )}
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${STATUS_STYLE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </div>

      {/* 子主题状态列表 */}
      {topics.length > 0 && (
        <div className="min-w-0 rounded-md border border-rule bg-paper/40 p-3">
          <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
            <span className="font-sans text-xs font-medium text-ink">
              调研子主题 ({topics.filter((t) => t.status === "completed").length}/{topics.length})
            </span>
            <span className="hidden truncate font-mono text-[11px] text-ink-muted sm:block" title="支持各主题独立取证与单点取消">
              支持各主题独立取证与单点取消
            </span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {topics.map((t) => {
              const canCancel =
                Boolean(onCancelTopic) &&
                (t.status === "running" ||
                  t.status === "queued" ||
                  (isCollecting && (t.status === "completed" || t.status === "failed")));
              return (
                <div
                  key={t.id}
                  className="flex min-w-0 items-start justify-between rounded border border-rule/80 bg-surface p-2 text-xs shadow-sm"
                >
                  <div className="min-w-0 flex-1 pr-2">
                    <div className="flex min-w-0 items-start gap-1.5">
                      <span className="shrink-0 font-mono text-[10px] text-ink-muted">{t.id}</span>
                      <span className="min-w-0 flex-1 line-clamp-2 break-words font-medium text-ink" title={t.name}>
                        {t.name}
                      </span>
                    </div>
                    {t.latest_progress && (
                      <p className="mt-1 line-clamp-2 break-words font-sans text-[11px] text-ink-muted" title={t.latest_progress}>
                        {formatProgressMessage(t.latest_progress)}
                      </p>
                    )}
                    {t.error && (
                      <p className="mt-1 line-clamp-2 break-words text-[11px] font-medium text-danger" title={t.error}>
                        {formatProgressMessage(t.error)}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span
                      className={`rounded border px-1.5 py-0.5 font-sans text-[10px] ${
                        TOPIC_STYLE[t.status] || "border-rule text-ink-muted"
                      }`}
                    >
                      {TOPIC_LABEL[t.status] || t.status}
                    </span>
                    {canCancel && (
                      <button
                        type="button"
                        onClick={() => onCancelTopic?.(t.id)}
                        className="rounded border border-rule px-1.5 py-0.5 text-[10px] text-ink-muted hover:border-danger hover:bg-red-50 hover:text-danger"
                        title={t.status === "completed" ? "从本轮报告移除该主题" : "取消该主题"}
                      >
                        {t.status === "completed" ? "移除" : "取消"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 歧义澄清交互提示 */}
      {clarificationOptions.length > 0 && (
        <div className="min-w-0 rounded-md border border-accent/30 bg-accent-soft p-3 text-xs">
          <p className="font-medium text-accent">请选择要操作的目标主题：</p>
          <div className="mt-2 flex min-w-0 flex-wrap gap-1.5">
            {clarificationOptions.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => onCancelTopic?.(opt.id)}
                className="min-w-0 max-w-full rounded-md border border-accent/40 bg-surface px-2.5 py-1 text-left text-xs text-ink transition hover:bg-paper"
                title={`${opt.name} (${opt.id})`}
              >
              <span className="line-clamp-2 break-words [overflow-wrap:anywhere]">{opt.name} ({opt.id})</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <ol className="relative ml-2 min-w-0 space-y-3.5 border-l border-rule pl-4 text-xs font-sans">
        {events.map((event, index) => {
          const isLatestRunning = status === "running" && index === events.length - 1;
          const progressDetails = formatProgressDetails(event.details);
          return (
            <li key={event.id} className="relative flex min-w-0 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
              <span
                className={`absolute -left-[21px] top-1.5 h-2 w-2 rounded-full border ${
                  isLatestRunning
                    ? "border-highlight bg-highlight"
                    : "border-rule bg-surface"
                }`}
              />
              <div className="flex min-w-0 items-center gap-1.5 sm:max-w-[55%]">
                <span className="shrink-0 font-mono text-[11px] text-ink-muted">
                  {event.timestamp.slice(11) || "--:--:--"}
                </span>
                <span
                  className={`min-w-0 truncate font-sans font-medium ${
                    isLatestRunning ? "text-highlight" : "text-ink-muted"
                  }`}
                >
                  [{event.stage}]
                  {event.topic_name && <span className="ml-1 text-ink">({event.topic_name})</span>}
                </span>
              </div>
              <div className="min-w-0 flex-1">
                <span
                  className={`block min-w-0 line-clamp-2 break-words ${
                    isLatestRunning ? "font-medium text-highlight" : "text-ink"
                  }`}
                  title={event.message}
                >
                  {formatProgressMessage(event.message)}
                </span>
                {progressDetails && (
                  <span
                    className="mt-0.5 block min-w-0 max-w-full truncate font-mono text-[10px] text-ink-muted"
                    title={progressDetails}
                  >
                    {progressDetails}
                  </span>
                )}
              </div>
            </li>
          );
        })}
        {events.length === 0 && status === "running" && (
          <li className="relative flex min-w-0 items-center gap-2">
            <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full border border-highlight bg-highlight" />
            <span className="text-highlight">等待第一条进度…</span>
          </li>
        )}
      </ol>

      {error && (
        <p className="mt-3 line-clamp-2 break-words text-sm font-medium text-danger" title={error}>
          任务失败：{formatProgressMessage(error)}
        </p>
      )}
    </section>
  );
}
