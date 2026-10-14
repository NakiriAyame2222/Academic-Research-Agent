import { useEffect, useRef, useState } from "react";

import { useJobStream } from "../api/useJobStream";
import type { ConversationTurn } from "../api/client";
import { ProgressLog } from "./ProgressLog";
import { ReportView } from "./ReportView";
import type { JobStatus, ResearchResult } from "../types";

interface TurnBlockProps {
  query: string;
  timestamp?: string;
  turnIndex?: number;
  isFollowUp?: boolean;
  /** 进行中的轮：传 jobId 订阅进度流；完成时回调 onComplete */
  jobId?: string;
  /** 历史轮：直接给结果 */
  historyResult?: ResearchResult | null;
  onCitationClick: (citationId: string) => void;
  onCancel?: () => void;
  onCancelTopic?: (topicId: string) => void;
  onComplete?: (turn: ConversationTurn) => void;
  onTerminal?: (status: JobStatus, error?: string) => void;
}

/** 一轮对话：用户问题气泡 + 该轮的进度流与报告。 */
export function TurnBlock({
  query,
  timestamp,
  turnIndex,
  isFollowUp,
  jobId,
  historyResult,
  onCitationClick,
  onCancel,
  onCancelTopic,
  onComplete,
  onTerminal,
}: TurnBlockProps) {
  const {
    events,
    status,
    phase,
    topics,
    clarificationOptions,
    result: liveResult,
    error,
  } = useJobStream(jobId ?? null);
  const [collapsed, setCollapsed] = useState(false);
  const reported = useRef(false);

  const result = historyResult ?? liveResult;
  const isLive = jobId !== undefined;

  useEffect(() => {
    if (isLive && status === "succeeded" && liveResult && onComplete && !reported.current) {
      reported.current = true;
      onComplete({
        index: turnIndex ?? 0,
        query,
        timestamp: new Date().toISOString(),
        result: liveResult,
      });
    } else if (isLive && (status === "cancelled" || status === "failed") && !reported.current) {
      reported.current = true;
      onTerminal?.(status, error);
    }
  }, [isLive, status, liveResult, onComplete, onTerminal, error, query, turnIndex]);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-xl rounded-br-sm bg-ink px-4 py-2.5 font-sans text-sm text-paper shadow-sm">
          {query}
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-rule bg-surface shadow-sm">
        {isLive ? (
          <div className="px-4 pt-3">
            <ProgressLog
              events={events}
              status={status}
              phase={phase}
              topics={topics}
              clarificationOptions={clarificationOptions}
              error={error}
              onCancelTopic={onCancelTopic}
            />
            {status === "running" && onCancel && (
              <button
                onClick={onCancel}
                className="mb-3 mt-2 rounded-md border border-rule px-3 py-1 font-sans text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
              >
                取消任务
              </button>
            )}
          </div>
        ) : (
          <button
            onClick={() => setCollapsed((prev) => !prev)}
            className="flex w-full items-center justify-between border-b border-rule/60 bg-paper/30 px-4 py-2.5 font-sans text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
          >
            <span className="flex items-center gap-1.5 font-mono text-[11px]">
              <span>{timestamp}</span>
              {turnIndex !== undefined && <span className="font-sans">· 第 {turnIndex + 1} 轮</span>}
            </span>
            <span className="font-sans text-xs">{collapsed ? "展开 ▸" : "收起 ▾"}</span>
          </button>
        )}

        {!collapsed && result && (
          <ReportView
            result={result}
            onCitationClick={onCitationClick}
            isFollowUp={isFollowUp ?? (turnIndex !== undefined && turnIndex > 0)}
          />
        )}
      </div>
    </div>
  );
}
