import React, { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ResearchResult } from "../types";
import {
  FOLLOW_UP_DISCUSSION_SOURCES,
  getDiscussionIndicator,
} from "./discussion";

interface ReportViewProps {
  result: ResearchResult;
  onCitationClick: (citationId: string) => void;
  isFollowUp?: boolean;
}

const REMARK_PLUGINS = [remarkGfm];

/** 把正文里的 [Sx] 转成可点击链接；后端就地标注过的非法编号（[S9?未匹配到检索证据]）转成红色标记。 */
function linkifyCitations(text: string): string {
  return text
    .replace(/\[S(\d+)\?未匹配到检索证据\]/g, (_match, num: string) => `[S${num}✗](#invalid-S${num})`)
    .replace(/\[S(\d+)\]/g, (_match, num: string) => `[S${num}](#cite-S${num})`);
}

export const ReportView = React.memo(function ReportView({
  result,
  onCitationClick,
  isFollowUp = false,
}: ReportViewProps) {
  const audit = result.citation_audit;
  const discussionIndicator = useMemo(() => getDiscussionIndicator(result), [result]);

  const formattedMarkdown = useMemo(
    () => linkifyCitations(result.final_answer),
    [result.final_answer],
  );

  const components: Components = useMemo(
    () => ({
      a: ({ href, children }) => {
        if (href && href.startsWith("#cite-S")) {
          const citationId = href.slice("#cite-".length);
          return (
            <button
              type="button"
              onClick={() => onCitationClick(citationId)}
              className="mx-0.5 inline-flex items-baseline rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[0.8em] font-medium text-accent transition hover:bg-accent/20"
            >
              {children}
            </button>
          );
        }
        if (href && href.startsWith("#invalid-S")) {
          return (
            <span className="font-mono font-semibold text-danger" title="未匹配到检索证据">
              {children}
            </span>
          );
        }
        return (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="text-accent underline underline-offset-2 hover:text-[#203a6b]"
          >
            {children}
          </a>
        );
      },
    }),
    [onCitationClick],
  );

  return (
    <section className="report-fade-in rounded-lg border border-rule bg-surface p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-sans text-sm font-medium text-ink">研究报告</h2>
        <div className="flex items-center gap-2 text-xs">
          {audit && (
            <span className="font-sans text-xs text-ink-muted">
              引用覆盖率 {(audit.citation_coverage * 100).toFixed(0)}%
            </span>
          )}
          {audit && audit.invalid_references.length > 0 && (
            <span className="font-sans text-xs text-danger" title="报告中未能对应到检索证据的编号">
              非法编号 {audit.invalid_references.join("、")}
            </span>
          )}
          {result.report_download_url && (
            <a
              href={result.report_download_url}
              className="rounded border border-rule bg-surface px-2 py-0.5 font-sans text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
            >
              下载 .md
            </a>
          )}
        </div>
      </div>

      {isFollowUp && (
        <div
          className="mb-4 flex flex-wrap items-center gap-1.5 rounded-md border border-accent/20 bg-accent-soft/50 px-3 py-2 font-sans text-xs text-ink-muted"
          title="追问回答综合当前研究报告、已选论文笔记，并按需补充取证"
        >
          <span className="font-medium text-ink">追问依据</span>
          <span className="text-ink-muted">·</span>
          {(discussionIndicator?.sources.length ? discussionIndicator.sources : FOLLOW_UP_DISCUSSION_SOURCES).map((source) => (
            <span key={source} className="rounded bg-surface px-1.5 py-0.5">
              {source}
            </span>
          ))}
          {!discussionIndicator?.sources.length && (
            <span className="text-ink-muted">（全文证据按问题需要检索）</span>
          )}
        </div>
      )}

      {discussionIndicator && (
        <div
          className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 font-sans text-[11px] text-ink-muted"
          aria-label="讨论范围与来源"
        >
          {discussionIndicator.scope && (
            <span>
              <span className="font-medium text-ink-muted">讨论范围</span>：{discussionIndicator.scope}
            </span>
          )}
          {discussionIndicator.sources.length > 0 && (
            <span>
              <span className="font-medium text-ink-muted">来源</span>：{discussionIndicator.sources.join(" · ")}
            </span>
          )}
        </div>
      )}

      <article className="prose max-w-[72ch] font-serif text-[16px] leading-[1.7] text-ink">
        <ReactMarkdown
          remarkPlugins={REMARK_PLUGINS}
          components={components}
        >
          {formattedMarkdown}
        </ReactMarkdown>
      </article>
    </section>
  );
});
