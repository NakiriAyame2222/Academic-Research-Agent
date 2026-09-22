import type { Citation } from "../types";

interface CitationPanelProps {
  citations: Citation[];
  /** 报告内 [Sx] 锚点点击时的联动高亮 */
  highlightId: string | null;
}

/** ① 证据 Citations：报告里的 [Sx] 可点击滚动到这里并高亮。 */
export function CitationPanel({ citations, highlightId }: CitationPanelProps) {
  if (citations.length === 0) {
    return <p className="text-xs text-slate-400">本轮无引用</p>;
  }
  return (
    <ul className="space-y-2">
      {citations.map((citation) => {
        const highlighted = highlightId === citation.id;
        const locator = citation.page !== null ? `p.${citation.page}` : `chunk ${citation.chunk_index}`;
        return (
          <li
            key={`${citation.id}-${citation.chunk_index}`}
            id={`citation-${citation.id}`}
            className={`rounded-md border p-2 text-xs transition ${
              highlighted ? "border-blue-400 bg-blue-50 ring-1 ring-blue-300" : "border-slate-200"
            }`}
          >
            <div className="flex items-center gap-1.5 font-medium">
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-white">
                {citation.id}
              </span>
              <span className="flex-1 truncate" title={citation.paper_title || citation.source_name}>
                {citation.paper_title || citation.source_name || citation.source}
              </span>
              <span className="shrink-0 font-mono text-slate-400">{locator}</span>
            </div>
            {citation.arxiv_id && (
              <a
                href={`https://arxiv.org/abs/${citation.arxiv_id}`}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-[11px] text-blue-600 hover:underline"
              >
                arXiv:{citation.arxiv_id}
              </a>
            )}
            {citation.excerpt && (
              <p className="mt-1 line-clamp-3 text-slate-500" title={citation.excerpt}>
                {citation.excerpt}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
