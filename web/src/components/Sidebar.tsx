import { useEffect, useState } from "react";

import { CitationPanel } from "./CitationPanel";
import { EvidenceStatusPanel } from "./EvidenceStatusPanel";
import { ReflectionPanel } from "./ReflectionPanel";
import { ToolTracePanel } from "./ToolTracePanel";
import type { ResearchResult } from "../types";

type TabKey = "citations" | "status";

const TABS: { key: TabKey; label: string }[] = [
  { key: "citations", label: "来源证据" },
  { key: "status", label: "研究状态" },
];

interface SidebarProps {
  result: ResearchResult;
  highlightId: string | null;
}

/** 常用信息分成来源与研究状态，完整工具轨迹按需展开。 */
export function Sidebar({ result, highlightId }: SidebarProps) {
  const [tab, setTab] = useState<TabKey>("citations");

  useEffect(() => {
    if (highlightId) {
      setTab("citations");
    }
  }, [highlightId]);

  return (
    <aside className="flex w-80 shrink-0 flex-col rounded-lg border border-rule bg-surface p-3.5 shadow-sm">
      <div className="mb-3 flex gap-1 border-b border-rule">
        {TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            className={`px-2.5 py-1.5 font-sans text-xs font-medium transition ${
              tab === item.key
                ? "-mb-px border-b-2 border-accent text-ink"
                : "-mb-px border-b-2 border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {tab === "citations" && (
          <>
            <CitationPanel citations={result.citations} highlightId={highlightId} />
            {result.evidence_status.length > 0 && (
              <details className="mt-3 border-t border-rule pt-3">
                <summary className="mb-2 cursor-pointer text-xs text-ink-muted">各论文证据归属</summary>
                <EvidenceStatusPanel evidenceStatus={result.evidence_status} />
              </details>
            )}
          </>
        )}
        {tab === "status" && (
          <>
            <ReflectionPanel result={result} />
            <details className="mt-3 border-t border-rule pt-3">
              <summary className="mb-2 cursor-pointer text-xs text-ink-muted">调试详情 · 工具调用记录</summary>
              <ToolTracePanel trace={result.tool_trace} degraded={result.tool_loop_degraded} />
            </details>
          </>
        )}
      </div>
    </aside>
  );
}
