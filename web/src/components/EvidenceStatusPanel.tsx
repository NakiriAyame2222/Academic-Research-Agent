import type { EvidenceStatusItem } from "../types";

interface EvidenceStatusPanelProps {
  evidenceStatus: EvidenceStatusItem[];
}

const STATUS_LABEL: Record<string, { label: string; style: string }> = {
  arxiv_id: { label: "arxiv_id", style: "bg-green-100 text-green-700" },
  source_name_prefix: { label: "source_name 前缀", style: "bg-blue-100 text-blue-700" },
  paper_title: { label: "paper_title", style: "bg-teal-100 text-teal-700" },
  fallback: { label: "fallback", style: "bg-amber-100 text-amber-700" },
};

/** ③ 证据归属：每篇论文的证据匹配方式打标签，fallback 标黄。 */
export function EvidenceStatusPanel({ evidenceStatus }: EvidenceStatusPanelProps) {
  if (evidenceStatus.length === 0) {
    return <p className="text-xs text-slate-400">无证据归属信息</p>;
  }
  return (
    <ul className="space-y-1.5">
      {evidenceStatus.map((item) => {
        const badge = STATUS_LABEL[item.evidence_status] ?? {
          label: item.evidence_status,
          style: "bg-slate-100 text-slate-600",
        };
        return (
          <li key={`${item.title}-${item.arxiv_id}`} className="rounded-md border border-slate-200 p-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="flex-1 truncate" title={item.title}>
                {item.title}
              </span>
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${badge.style}`}>
                {badge.label}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-3 text-slate-500">
              {item.arxiv_id && <span className="font-mono">arXiv:{item.arxiv_id}</span>}
              <span>{item.evidence_count} 个证据片段</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
