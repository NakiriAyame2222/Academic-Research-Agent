import type { ToolTraceItem } from "../types";

interface ToolTracePanelProps {
  trace: ToolTraceItem[];
  degraded: boolean;
}

/** 调试详情：工具参数、结果摘要与耗时。 */
export function ToolTracePanel({ trace, degraded }: ToolTracePanelProps) {
  if (trace.length === 0) {
    return <p className="font-sans text-xs text-ink-muted">{degraded ? "本轮已使用备用检索流程，无可用工具调用记录" : "本轮无工具调用"}</p>;
  }
  return (
    <ol className="space-y-2">
      {trace.map((item) => (
        <li key={item.step} className="rounded-md border border-rule bg-surface p-2.5 text-xs font-sans">
          <div className="flex items-center gap-2 font-mono">
            <span className="text-ink-muted">#{item.step}</span>
            <span className="font-medium text-ink">{item.tool}</span>
            <span className="ml-auto shrink-0 rounded border border-rule bg-paper px-1.5 py-0.5 text-[10px] text-ink-muted">
              {item.elapsed_seconds.toFixed(2)}s
            </span>
          </div>
          <p className="mt-1.5 truncate font-mono text-[11px] text-ink-muted/80" title={JSON.stringify(item.arguments)}>
            {JSON.stringify(item.arguments)}
          </p>
          <p className="mt-0.5 truncate text-ink" title={item.result_summary}>
            → {item.result_summary}
          </p>
        </li>
      ))}
    </ol>
  );
}
