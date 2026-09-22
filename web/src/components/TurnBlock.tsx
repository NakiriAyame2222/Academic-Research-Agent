import { useEffect, useRef, useState } from "react";

import { useJobStream } from "../api/useJobStream";
import type { ConversationTurn } from "../api/client";
import { ProgressLog } from "./ProgressLog";
import { ReportView } from "./ReportView";
import type { ResearchResult } from "../types";

interface TurnBlockProps {
  query: string;
  timestamp?: string;
  turnIndex?: number;
  /** 进行中的轮：传 jobId 订阅进度流；完成时回调 onComplete */
  jobId?: string;
  /** 历史轮：直接给结果 */
  historyResult?: ResearchResult | null;
  onCitationClick: (citationId: string) => void;
  onCancel?: () => void;
  onComplete?: (turn: ConversationTurn) => void;
}

/** 一轮对话：用户问题气泡 + 该轮的进度流与报告。 */
export function TurnBlock({
  query,
  timestamp,
  turnIndex,
  jobId,
  historyResult,
  onCitationClick,
  onCancel,
  onComplete,
}: TurnBlockProps) {
  const { events, status, result: liveResult, error } = useJobStream(jobId ?? null);
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
    }
  }, [isLive, status, liveResult, onComplete, query, turnIndex]);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-slate-800 px-4 py-2 text-sm text-white">
          {query}
        </div>
      </div>

      <div className="rounded-lg border bg-white">
        {isLive ? (
          <div className="px-4 pt-3">
            <ProgressLog events={events} status={status} error={error} />
            {status === "running" && onCancel && (
              <button
                onClick={onCancel}
                className="mb-3 rounded-md border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100"
              >
                取消任务
              </button>
            )}
          </div>
        ) : (
          <button
            onClick={() => setCollapsed((prev) => !prev)}
            className="flex w-full items-center justify-between px-4 py-2 text-xs text-slate-400 hover:text-slate-600"
          >
            <span>
              {timestamp} {turnIndex !== undefined && `· 第 ${turnIndex + 1} 轮`}
            </span>
            <span>{collapsed ? "展开 ▸" : "收起 ▾"}</span>
          </button>
        )}

        {!collapsed && result && <ReportView result={result} onCitationClick={onCitationClick} />}
      </div>
    </div>
  );
}
