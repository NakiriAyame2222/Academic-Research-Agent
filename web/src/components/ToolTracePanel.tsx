import type { ToolTraceItem } from "../types";

interface ToolTracePanelProps {
  trace: ToolTraceItem[];
  degraded: boolean;
}

/** ② 工具轨迹：step/tool/耗时时间线 —— 直观证明“这是 agent 不是写死的 workflow”。 */
export function ToolTracePanel({ trace, degraded }: ToolTracePanelProps) {
  if (degraded) {
    return (
      <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-700">
        ⚠ 当前端点不支持原生 tool calling，已降级为手工 JSON 模式运行
      </p>
    );
  }
  if (trace.length === 0) {
    return <p className="text-xs text-slate-400">本轮无工具调用</p>;
  }
  return (
    <ol className="space-y-1.5">
      {trace.map((item) => (
        <li key={item.step} className="rounded-md border border-slate-200 p-2 text-xs">
          <div className="flex items-center gap-2 font-mono">
            <span className="text-slate-400">#{item.step}</span>
            <span className="font-medium text-slate-700">{item.tool}</span>
            <span className="ml-auto shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
              {item.elapsed_seconds.toFixed(2)}s
            </span>
          </div>
          <p className="mt-1 truncate text-slate-500" title={JSON.stringify(item.arguments)}>
            {JSON.stringify(item.arguments)}
          </p>
          <p className="truncate text-slate-600" title={item.result_summary}>
            → {item.result_summary}
          </p>
        </li>
      ))}
    </ol>
  );
}
