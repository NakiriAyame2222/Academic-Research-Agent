import { useRef, useState } from "react";

import {
  cancelJob,
  createSession,
  getConversation,
  submitResearch,
  type ConversationTurn,
} from "./api/client";
import { useHealth } from "./api/useHealth";
import { HealthBar } from "./components/HealthBar";
import { MemoryConfirm } from "./components/MemoryConfirm";
import { QueryBar } from "./components/QueryBar";
import { SessionBar } from "./components/SessionBar";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { TurnBlock } from "./components/TurnBlock";
import type { SessionSummary } from "./types";

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<ConversationTurn[]>([]); // 已完成的轮
  const [activeJobId, setActiveJobId] = useState<string | null>(null); // 进行中的轮
  const [activeQuery, setActiveQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [uploadedFile, setUploadedFile] = useState<{ path: string; name: string } | null>(null);
  const [focusResult, setFocusResult] = useState<import("./types").ResearchResult | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [showMemory, setShowMemory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsVersion, setSettingsVersion] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  // settings 变更后重新拉 health（LLM 连接状态可能已改变）
  const health = useHealth(settingsVersion);

  async function handleSubmit(query: string, filePath: string | null) {
    setSubmitError("");
    setSubmitting(true);
    try {
      let sid = sessionId;
      if (!sid) {
        sid = (await createSession()).session_id;
        setSessionId(sid);
      }
      const { job_id } = await submitResearch(sid, {
        query,
        file_path: filePath ?? undefined,
        // 会话里已有完成的轮 → 多轮续跑（agent 侧 resume=True 读 checkpoint）
        resume: turns.length > 0,
      });
      setActiveQuery(query);
      setActiveJobId(job_id);
      setShowMemory(false);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function handleTurnComplete(turn: ConversationTurn) {
    // 以服务器记账的 index 为准（后端 append_turn 写入的序号）
    setTurns((prev) => [...prev, { ...turn, index: prev.length }]);
    setActiveJobId(null);
    setFocusResult(turn.result);
  }

  async function handleSelectSession(session: SessionSummary) {
    setActiveJobId(null);
    setShowMemory(false);
    if (!session.session_id) {
      // "新会话"
      setSessionId(null);
      setTurns([]);
      setFocusResult(null);
      return;
    }
    setSessionId(session.session_id);
    try {
      const data = await getConversation(session.session_id);
      setTurns(data.turns);
      setFocusResult(data.turns.length ? data.turns[data.turns.length - 1].result : null);
    } catch {
      setTurns([]);
      setFocusResult(null);
    }
  }

  function handleCitationClick(citationId: string) {
    setHighlightId(citationId);
    document.getElementById(`citation-${citationId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setHighlightId(null), 2000);
  }

  const running = submitting || activeJobId !== null;
  const lastResult = focusResult ?? turns[turns.length - 1]?.result ?? null;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <header className="border-b bg-white px-6 py-3">
        <div className="mb-2 flex items-center gap-3">
          <h1 className="text-lg font-semibold">学术研究 Agent</h1>
          <SessionBar currentSessionId={sessionId} onSelect={handleSelectSession} />
          <button
            onClick={() => setShowSettings(true)}
            className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="模型与 API 配置"
          >
            ⚙ 模型配置
          </button>
        </div>
        <HealthBar health={health} />
      </header>

      {showSettings && (
        <SettingsDialog
          onClose={() => setShowSettings(false)}
          onSaved={() => setSettingsVersion((v) => v + 1)}
        />
      )}

      <main className="mx-auto flex max-w-7xl gap-6 px-6 py-6">
        <div className="min-w-0 flex-1 space-y-6">
          {/* 对话流：已完成的历史轮 */}
          {turns.map((turn) => (
            <TurnBlock
              key={`${turn.index}-${turn.timestamp}`}
              query={turn.query}
              timestamp={turn.timestamp}
              turnIndex={turn.index}
              historyResult={turn.result}
              onCitationClick={handleCitationClick}
            />
          ))}

          {/* 进行中的一轮 */}
          {activeJobId && (
            <TurnBlock
              jobId={activeJobId}
              query={activeQuery}
              onComplete={handleTurnComplete}
              onCitationClick={handleCitationClick}
              onCancel={() => cancelJob(activeJobId).catch(() => undefined)}
            />
          )}

          {turns.length === 0 && !activeJobId && (
            <p className="pt-8 text-center text-sm text-slate-400">
              提交一个调研主题或论文问题开始对话
            </p>
          )}

          {submitError && <p className="text-sm text-red-600">提交失败：{submitError}</p>}

          <div ref={bottomRef} />

          <div className="sticky bottom-4 rounded-lg border bg-white/95 p-3 shadow-md backdrop-blur">
            <QueryBar
              onSubmit={handleSubmit}
              disabled={running}
              uploadedFile={uploadedFile}
              onUploaded={setUploadedFile}
            />
          </div>

          {lastResult && lastResult.pending_memory_candidates.length > 0 && (
            <button
              onClick={() => setShowMemory((prev) => !prev)}
              className="rounded-md border border-pink-300 bg-pink-50 px-3 py-1.5 text-xs text-pink-700"
            >
              {showMemory ? "收起" : `待确认记忆 ${lastResult.pending_memory_candidates.length} 条`}
            </button>
          )}
          {showMemory && sessionId && (
            <MemoryConfirm
              sessionId={sessionId}
              onDone={() => {
                setShowMemory(false);
                setFocusResult((prev) =>
                  prev ? { ...prev, pending_memory_candidates: [] } : prev,
                );
              }}
            />
          )}
        </div>

        {lastResult && (
          <div className="hidden lg:block">
            <Sidebar result={lastResult} highlightId={highlightId} />
          </div>
        )}
      </main>
    </div>
  );
}
