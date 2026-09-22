import type { JobStatus, ProgressEvent } from "../types";

// 按 stage 上色，直观区分 planner / search / tool / research 等阶段。
const STAGE_COLORS: Record<string, string> = {
  planner: "bg-purple-100 text-purple-700",
  plan: "bg-purple-100 text-purple-700",
  search: "bg-blue-100 text-blue-700",
  select: "bg-blue-100 text-blue-700",
  tool: "bg-amber-100 text-amber-700",
  fulltext: "bg-teal-100 text-teal-700",
  notes: "bg-teal-100 text-teal-700",
  research: "bg-green-100 text-green-700",
  reflect: "bg-orange-100 text-orange-700",
  rewrite: "bg-orange-100 text-orange-700",
  report: "bg-slate-200 text-slate-700",
  memory: "bg-pink-100 text-pink-700",
};

const STATUS_STYLE: Record<JobStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  running: "bg-blue-100 text-blue-700",
  succeeded: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-500",
};

const STATUS_LABEL: Record<JobStatus, string> = {
  pending: "等待中",
  running: "研究中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
};

interface ProgressLogProps {
  events: ProgressEvent[];
  status: JobStatus;
  error: string;
}

export function ProgressLog({ events, status, error }: ProgressLogProps) {
  return (
    <section className="rounded-lg border bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">进度流</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </div>

      <ol className="space-y-1.5 font-mono text-xs">
        {events.map((event) => (
          <li key={event.id} className="flex gap-2">
            <span className="shrink-0 text-slate-400">{event.timestamp.slice(11) || "--:--:--"}</span>
            <span className={`shrink-0 rounded px-1.5 ${STAGE_COLORS[event.stage] ?? "bg-slate-100 text-slate-600"}`}>
              {event.stage}
            </span>
            <span className="text-slate-700">{event.message}</span>
          </li>
        ))}
        {events.length === 0 && status === "running" && (
          <li className="text-slate-400">等待第一条进度…</li>
        )}
      </ol>

      {error && <p className="mt-3 text-sm text-red-600">任务失败：{error}</p>}
    </section>
  );
}
