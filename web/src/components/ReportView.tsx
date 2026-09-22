import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ResearchResult } from "../types";

interface ReportViewProps {
  result: ResearchResult;
  onCitationClick: (citationId: string) => void;
}

/** 把正文里的 [Sx] 转成可点击链接；后端就地标注过的非法编号（[S9?未匹配到检索证据]）转成红色标记。 */
function linkifyCitations(text: string): string {
  return text
    .replace(/\[S(\d+)\?未匹配到检索证据\]/g, (_match, num: string) => `[S${num}✗](#invalid-S${num})`)
    .replace(/\[S(\d+)\]/g, (_match, num: string) => `[S${num}](#cite-S${num})`);
}

export function ReportView({ result, onCitationClick }: ReportViewProps) {
  const audit = result.citation_audit;

  return (
    <section className="rounded-lg border bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">研究报告</h2>
        <div className="flex items-center gap-2 text-xs">
          {audit && (
            <span className="text-slate-500">
              引用覆盖率 {(audit.citation_coverage * 100).toFixed(0)}%
            </span>
          )}
          {audit && audit.invalid_references.length > 0 && (
            <span className="rounded bg-red-100 px-1.5 py-0.5 text-red-700" title="报告中未能对应到检索证据的编号">
              非法编号 {audit.invalid_references.join("、")}
            </span>
          )}
          {result.report_download_url && (
            <a
              href={result.report_download_url}
              className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600 hover:bg-slate-200"
            >
              下载 .md
            </a>
          )}
        </div>
      </div>

      <article className="prose prose-sm max-w-none prose-slate">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href && href.startsWith("#cite-S")) {
                const citationId = href.slice("#cite-".length);
                return (
                  <button
                    type="button"
                    onClick={() => onCitationClick(citationId)}
                    className="rounded bg-blue-50 px-1 font-mono text-[0.85em] text-blue-700 hover:bg-blue-100"
                  >
                    {children}
                  </button>
                );
              }
              if (href && href.startsWith("#invalid-S")) {
                return (
                  <span className="font-mono font-semibold text-red-600" title="未匹配到检索证据">
                    {children}
                  </span>
                );
              }
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            },
          }}
        >
          {linkifyCitations(result.final_answer)}
        </ReactMarkdown>
      </article>
    </section>
  );
}
