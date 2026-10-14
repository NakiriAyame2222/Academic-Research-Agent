import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelJob,
  cancelTopic,
  createSession,
  getConversation,
  getCurrentResearch,
  resumeResearch,
  sendResearchCommand,
  submitResearch,
  type ConversationTurn,
  type RetrievalOptions,
  type SelectedFile,
} from "./api/client";
import { NoteEditor } from "./components/NoteEditor";
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
  const [loadError, setLoadError] = useState("");
  const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null);
  const [retrievalOptions, setRetrievalOptions] = useState<RetrievalOptions>({ rebuild_index: false });
  const [loadingSession, setLoadingSession] = useState(false);
  const sessionLoadVersion = useRef(0);
  const [focusResult, setFocusResult] = useState<import("./types").ResearchResult | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [showNote, setShowNote] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [interruptedState, setInterruptedState] = useState<{ canResume: boolean; topicsCount: number } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("academic_agent_last_session_id");
      if (saved) {
        handleSelectSession({
          session_id: saved,
          topic: "",
          has_report: false,
          updated_at: "",
          messages_count: 0,
        });
      }
    } catch {}
  }, []);

  async function handleSubmit(query: string, filePath: string | null) {
    if (retrievalOptions.top_k !== undefined &&
        (!Number.isInteger(retrievalOptions.top_k) || retrievalOptions.top_k < 1 || retrievalOptions.top_k > 20)) {
      setSubmitError("检索数量应为 1 到 20 的整数。");
      return;
    }
    setSubmitError("");
    setLoadError("");
    setSubmitting(true);
    try {
      let sid = sessionId;
      if (!sid) {
        sid = (await createSession()).session_id;
        setSessionId(sid);
      }
      try {
        localStorage.setItem("academic_agent_last_session_id", sid);
      } catch {}
      const { job_id } = await submitResearch(sid, {
        query,
        file_path: filePath ?? undefined,
        top_k: retrievalOptions.top_k,
        rebuild_index: !!filePath && retrievalOptions.rebuild_index,
        // 会话里已有完成的轮 → 多轮续跑（agent 侧 resume=True 读 checkpoint）
        resume: turns.length > 0,
      });
      setActiveQuery(query);
      setRetrievalOptions((prev) => ({ ...prev, rebuild_index: false }));
      setActiveJobId(job_id);
      setInterruptedState(null);
      setShowNote(false);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSendCommand(commandText: string) {
    if (!sessionId) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const resp = await sendResearchCommand(sessionId, { raw_text: commandText });
      if (resp.status === "rejected") {
        setSubmitError(resp.message || "指令被拒绝");
      }
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResume() {
    if (!sessionId) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const resp = await resumeResearch(sessionId);
      if (resp.job_id) {
        setActiveJobId(resp.job_id);
        setActiveQuery("继续中断的调研");
        setInterruptedState(null);
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
      } else {
        setSubmitError(resp.message || "恢复失败");
      }
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

  function handleSessionDeleted(deletedSessionId: string) {
    if (deletedSessionId === sessionId) {
      sessionLoadVersion.current += 1;
      setLoadingSession(false);
      setSessionId(null);
      setTurns([]);
      setFocusResult(null);
      setActiveJobId(null);
      setInterruptedState(null);
      setLoadError("");
      setSelectedFile(null);
      setRetrievalOptions({ rebuild_index: false });
    }
    try {
      if (localStorage.getItem("academic_agent_last_session_id") === deletedSessionId) {
        localStorage.removeItem("academic_agent_last_session_id");
      }
    } catch {}
  }

  async function handleSelectSession(session: SessionSummary) {
    const version = ++sessionLoadVersion.current;
    setSelectedFile(null);
    setRetrievalOptions({ rebuild_index: false });
    setSubmitError("");
    setActiveJobId(null);
    setShowNote(false);
    setLoadError("");
    setInterruptedState(null);
    setTurns([]);
    setFocusResult(null);
    if (!session.session_id) {
      setLoadingSession(false);
      // "新会话"
      setSessionId(null);
      setTurns([]);
      setFocusResult(null);
      try {
        localStorage.removeItem("academic_agent_last_session_id");
      } catch {}
      return;
    }
    setSessionId(session.session_id);
    setLoadingSession(true);
    try {
      localStorage.setItem("academic_agent_last_session_id", session.session_id);
    } catch {}
    try {
      const data = await getConversation(session.session_id);
      if (version !== sessionLoadVersion.current) return;
      setTurns(data.turns);
      setSelectedFile(data.selected_file);
      setFocusResult(data.turns.length ? data.turns[data.turns.length - 1].result : null);
    } catch (err) {
      if (version !== sessionLoadVersion.current) return;
      setTurns([]);
      setFocusResult(null);
      setLoadError(`加载会话失败：${String(err)}`);
    }

    try {
      const cur = await getCurrentResearch(session.session_id);
      if (version !== sessionLoadVersion.current) return;
      if (cur.is_active && cur.job_id) {
        setActiveJobId(cur.job_id);
        setActiveQuery("进行中的调研");
      } else if (cur.can_resume) {
        setInterruptedState({
          canResume: true,
          topicsCount: cur.topics.length,
        });
      }
    } catch {
      // ignore
    } finally {
      if (version === sessionLoadVersion.current) setLoadingSession(false);
    }
  }

  const handleCitationClick = useCallback((citationId: string) => {
    setHighlightId(citationId);
    window.setTimeout(() => setHighlightId(null), 2000);
  }, []);

  const running = submitting || loadingSession || activeJobId !== null;
  const lastResult = focusResult ?? turns[turns.length - 1]?.result ?? null;

  return (
    <div className="min-h-screen bg-paper font-sans text-ink">
      <header className="border-b border-rule bg-surface px-6 py-3.5">
        <div className="mb-2 flex items-center gap-3">
          <h1 className="text-lg font-medium tracking-tight text-ink">学术研究 Agent</h1>
          <SessionBar
            currentSessionId={sessionId}
            onSelect={handleSelectSession}
            onDeleted={handleSessionDeleted}
          />
          <button
            onClick={() => setShowSettings(true)}
            className="rounded-md border border-rule px-2.5 py-1 text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
            title="模型、输出偏好与运行参数"
          >
            ⚙ 研究设置
          </button>
        </div>
      </header>

      {showSettings && (
        <SettingsDialog
          onClose={() => setShowSettings(false)}
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
              isFollowUp={turns.length > 0}
              onComplete={handleTurnComplete}
              onCitationClick={handleCitationClick}
              onCancel={() => {
                cancelJob(activeJobId).catch(() => undefined);
                setActiveJobId(null);
              }}
              onCancelTopic={(topicId) => {
                if (sessionId) {
                  cancelTopic(sessionId, topicId)
                    .then((resp) => {
                      if (resp && resp.status === "rejected") {
                        setSubmitError(resp.message || "取消主题请求被拒绝");
                      }
                    })
                    .catch((err) => setSubmitError(String(err)));
                }
              }}
              onTerminal={(status, error) => {
                setTurns((prev) => [
                  ...prev,
                  {
                    index: prev.length,
                    query: activeQuery,
                    timestamp: new Date().toISOString(),
                    result: {
                      session_id: sessionId || "",
                      intent_mode: "topic_research",
                      final_answer: `> **任务已${status === "cancelled" ? "取消" : "失败"}**\n\n${error || "调研流程终止"}`,
                      survey_artifact: {},
                      citations: [],
                      citation_audit: null,
                      tool_trace: [],
                      tool_loop_degraded: false,
                      evidence_status: [],
                      evidence_gaps: [],
                      open_questions: [],
                      research_rounds: 0,
                      selected_papers: [],
                      paper_notes: [],
                      pending_memory_candidates: [],
                      report_download_url: null,
                    },
                  },
                ]);
                setActiveJobId(null);
                if (error) setSubmitError(error);
              }}
            />
          )}

          {turns.length === 0 && !activeJobId && !loadError && (
            <div className="py-14 text-center">
              <h2 className="font-serif text-2xl font-normal tracking-tight text-ink sm:text-3xl">
                想调研什么？
              </h2>
              <p className="mt-2.5 text-xs text-ink-muted sm:text-sm">
                输入研究课题、上传论文深入研读，或直接点击下方示例开始
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {[
                  "综述 latent variable reasoning 的最新进展",
                  "RLHF 与 DPO 的取舍",
                  "扩散模型在蛋白质结构预测的应用",
                ].map((example) => (
                  <button
                    key={example}
                    type="button"
                    disabled={running}
                    onClick={() => handleSubmit(example, selectedFile?.path ?? null)}
                    className="rounded-full border border-rule bg-surface px-3.5 py-1.5 text-xs text-ink-muted transition hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          {loadError && <p className="text-sm font-medium text-danger">{loadError}</p>}
          {submitError && <p className="text-sm font-medium text-danger">{submitError}</p>}

          <div ref={bottomRef} />

          {interruptedState?.canResume && !activeJobId && (
            <div className="flex items-center justify-between rounded-xl border border-yellow-300 bg-yellow-50 p-3.5 text-xs text-yellow-900 shadow-sm">
              <div>
                <span className="font-semibold">检测到上次未完成的调研任务（已中断）：</span>
                <span className="ml-1 text-yellow-800">
                  包含 {interruptedState.topicsCount} 个子主题，保留已完成成果，可继续未完成的主题。
                </span>
              </div>
              <button
                type="button"
                disabled={submitting}
                onClick={handleResume}
                className="rounded-md bg-yellow-600 px-3 py-1.5 font-medium text-white transition hover:bg-yellow-700 disabled:opacity-50"
              >
                {submitting ? "正在恢复…" : "继续执行 (Resume)"}
              </button>
            </div>
          )}

          <div className="sticky bottom-4 rounded-xl border border-rule bg-surface/95 p-3.5 shadow-sm backdrop-blur">
            <QueryBar
              key={sessionId || "new"}
              onSubmit={(q, fp) => {
                if (activeJobId) {
                  handleSendCommand(q);
                } else {
                  handleSubmit(q, fp);
                }
              }}
              submitting={submitting}
              disabled={loadingSession}
              selectedFile={selectedFile}
              onFileSelected={(file) => {
                setSelectedFile(file);
                setRetrievalOptions((prev) => ({ ...prev, rebuild_index: false }));
              }}
              retrievalOptions={retrievalOptions}
              onRetrievalOptionsChange={setRetrievalOptions}
              commandMode={activeJobId !== null}
              placeholder={
                activeJobId
                  ? "输入动态指令，例如：增加调研主题 d 或 取消调研主题 b"
                  : undefined
              }
              buttonText={activeJobId ? "发送指令" : undefined}
            />
          </div>

          {lastResult?.final_answer && !activeJobId && (
            <button
              onClick={() => setShowNote((prev) => !prev)}
              className="rounded-md border border-rule bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
            >
              {showNote ? "收起笔记编辑" : "保存研究笔记"}
            </button>
          )}
          {showNote && sessionId && !activeJobId && (
            <NoteEditor
              key={sessionId}
              sessionId={sessionId}
              report={lastResult?.final_answer || ""}
              onDone={() => setShowNote(false)}
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
