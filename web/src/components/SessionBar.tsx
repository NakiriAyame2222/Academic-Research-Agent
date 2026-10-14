import { useEffect, useState } from "react";

import { deleteSession, listSessions } from "../api/client";
import type { SessionSummary } from "../types";

interface SessionBarProps {
  currentSessionId: string | null;
  onSelect: (session: SessionSummary) => void;
  onDeleted?: (sessionId: string) => void;
}

/** 顶栏会话选择：扫 data/checkpoints/ 的会话列表，可切换/删除。 */
export function SessionBar({ currentSessionId, onSelect, onDeleted }: SessionBarProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setSessions(await listSessions());
    } catch {
      setSessions([]);
    }
  }

  useEffect(() => {
    refresh();
  }, [currentSessionId]);

  return (
    <div className="relative">
      <button
        onClick={() => {
          setError("");
          setOpen((prev) => !prev);
        }}
        className="rounded-md border border-rule px-2.5 py-1 font-sans text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
      >
        会话 {currentSessionId ? `(${currentSessionId.slice(0, 8)})` : "（新建）"} ▾
      </button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 w-72 rounded-lg border border-rule bg-surface p-2 shadow-lg font-sans">
          {error && <p className="mb-1.5 px-2 py-1 text-xs font-medium text-danger">{error}</p>}
          <button
            onClick={() => {
              setError("");
              onSelect({} as SessionSummary); // 空对象 → 父组件视为"新会话"
              setOpen(false);
            }}
            className="w-full rounded px-2 py-1.5 text-left text-xs font-medium text-accent transition hover:bg-accent-soft"
          >
            ＋ 新会话
          </button>
          {sessions.map((session) => (
            <div
              key={session.session_id}
              className={`flex items-center gap-1 rounded px-2 py-1.5 text-xs transition ${
                session.session_id === currentSessionId ? "bg-accent-soft text-accent" : "hover:bg-paper text-ink"
              }`}
            >
              <button
                className="min-w-0 flex-1 text-left"
                onClick={() => {
                  setError("");
                  onSelect(session);
                  setOpen(false);
                }}
                title={`${session.session_id} · ${session.updated_at}`}
              >
                <span className="font-mono text-[10px] text-ink-muted">{session.session_id.slice(0, 8)}</span>
                <span className="ml-1.5 block truncate text-ink">{session.topic || "（无主题）"}</span>
              </button>
              <button
                className="shrink-0 text-ink-muted/50 transition hover:text-danger"
                title="删除会话"
                onClick={async () => {
                  if (!confirm(`删除会话 ${session.session_id.slice(0, 8)}？`)) return;
                  setError("");
                  try {
                    await deleteSession(session.session_id);
                    onDeleted?.(session.session_id);
                    await refresh();
                  } catch (err) {
                    setError(`删除失败：${String(err)}`);
                  }
                }}
              >
                ✕
              </button>
            </div>
          ))}
          {sessions.length === 0 && <p className="px-2 py-1.5 text-xs text-ink-muted">暂无历史会话</p>}
        </div>
      )}
    </div>
  );
}
