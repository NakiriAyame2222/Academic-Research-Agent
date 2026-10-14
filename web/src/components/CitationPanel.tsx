import { useEffect } from "react";

import type { Citation } from "../types";

interface CitationPanelProps {
  citations: Citation[];
  /** 报告内 [Sx] 锚点点击时的联动高亮 */
  highlightId: string | null;
}

/** ① 证据 Citations：报告里的 [Sx] 可点击滚动到这里并高亮。 */
export function CitationPanel({ citations, highlightId }: CitationPanelProps) {
  useEffect(() => {
    if (highlightId) {
      document.getElementById(`citation-${highlightId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  }, [highlightId]);

  if (citations.length === 0) {
    return <p className="font-sans text-xs text-ink-muted">本轮无引用</p>;
  }
  return (
    <ul className="divide-y divide-rule font-sans">
      {citations.map((citation) => {
        const highlighted = highlightId === citation.id;
        const locator = citation.page !== null ? `p.${citation.page}` : `chunk ${citation.chunk_index}`;
        return (
          <li
            key={`${citation.id}-${citation.chunk_index}`}
            id={`citation-${citation.id}`}
            className={`py-2.5 text-xs transition-colors ${
              highlighted
                ? "citation-pulse -ml-1 rounded-r border-l-2 border-l-highlight bg-highlight/5 pl-2"
                : "pl-1 hover:bg-paper/40"
            }`}
          >
            <div className="flex items-baseline gap-1.5">
              <span className="font-mono text-xs font-semibold text-accent">
                [{citation.id}]
              </span>
              <span
                className="flex-1 truncate font-serif text-[13px] font-medium text-ink"
                title={citation.paper_title || citation.source_name}
              >
                {citation.paper_title || citation.source_name || citation.source}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-ink-muted">{locator}</span>
            </div>
            {citation.arxiv_id && (
              <a
                href={`https://arxiv.org/abs/${citation.arxiv_id}`}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block font-mono text-[11px] text-accent hover:underline"
              >
                arXiv:{citation.arxiv_id}
              </a>
            )}
            {citation.excerpt && (
              <p
                className="mt-1 line-clamp-3 font-serif text-[12px] leading-relaxed text-ink-muted"
                title={citation.excerpt}
              >
                “{citation.excerpt}”
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
