import { useState } from "react";

import { CitationPanel } from "./CitationPanel";
import { EvidenceStatusPanel } from "./EvidenceStatusPanel";
import { ReflectionPanel } from "./ReflectionPanel";
import { ToolTracePanel } from "./ToolTracePanel";
import type { ResearchResult } from "../types";

type TabKey = "citations" | "tooltrace" | "evidence" | "reflection";

const TABS: { key: TabKey; label: string }[] = [
  { key: "citations", label: "证据" },
  { key: "tooltrace", label: "工具轨迹" },
  { key: "evidence", label: "证据归属" },
  { key: "reflection", label: "反思" },
];

interface SidebarProps {
  result: ResearchResult;
  highlightId: string | null;
}

/** 侧栏：Tab 切换四块结构化数据（计划 5.1 的 ①②③④）。 */
export function Sidebar({ result, highlightId }: SidebarProps) {
  const [tab, setTab] = useState<TabKey>("citations");

  return (
    <aside className="flex w-80 shrink-0 flex-col rounded-lg border bg-white p-3">
      <div className="mb-3 flex gap-1 border-b">
        {TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            className={`rounded-t-md px-2.5 py-1.5 text-xs font-medium transition ${
              tab === item.key
                ? "border-b-2 border-slate-800 text-slate-900"
                : "border-b-2 border-transparent text-slate-400 hover:text-slate-600"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {tab === "citations" && <CitationPanel citations={result.citations} highlightId={highlightId} />}
        {tab === "tooltrace" && <ToolTracePanel trace={result.tool_trace} degraded={result.tool_loop_degraded} />}
        {tab === "evidence" && <EvidenceStatusPanel evidenceStatus={result.evidence_status} />}
        {tab === "reflection" && <ReflectionPanel result={result} />}
      </div>
    </aside>
  );
}
