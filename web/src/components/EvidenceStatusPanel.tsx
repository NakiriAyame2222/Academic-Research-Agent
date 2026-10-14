import type { EvidenceStatusItem } from "../types";

interface EvidenceStatusPanelProps {
  evidenceStatus: EvidenceStatusItem[];
}

const STATUS_LABEL: Record<string, { label: string; style: string }> = {
  arxiv_id: { label: "arxiv_id", style: "bg-paper text-ink-muted border border-rule" },
  source_name_prefix: { label: "source_name 前缀", style: "bg-paper text-ink-muted border border-rule" },
  paper_title: { label: "paper_title", style: "bg-paper text-ink-muted border border-rule" },
  fallback: { label: "fallback", style: "bg-highlight/10 text-highlight border border-highlight/30 font-medium" },
};

/** ③ 证据归属：每篇论文的证据匹配方式打标签，fallback 标黄。 */
export function EvidenceStatusPanel({ evidenceStatus }: EvidenceStatusPanelProps) {
  if (evidenceStatus.length === 0) {
    return <p className="font-sans text-xs text-ink-muted">无证据归属信息</p>;
  }
  return (
    <ul className="space-y-2 font-sans">
      {evidenceStatus.map((item) => {
        const badge = STATUS_LABEL[item.evidence_status] ?? {
          label: item.evidence_status,
          style: "bg-paper text-ink-muted border border-rule",
        };
        return (
          <li key={`${item.title}-${item.arxiv_id}`} className="rounded-md border border-rule bg-surface p-2.5 text-xs">
            <div className="flex items-center gap-2">
              <span className="flex-1 truncate font-serif text-[13px] font-medium text-ink" title={item.title}>
                {item.title}
              </span>
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${badge.style}`}>
                {badge.label}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-3 text-ink-muted">
              {item.arxiv_id && <span className="font-mono text-[11px]">arXiv:{item.arxiv_id}</span>}
              <span>{item.evidence_count} 个证据片段</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
