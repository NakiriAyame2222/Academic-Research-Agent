import { useEffect, useState } from "react";

import { deleteSession, listSessions } from "../api/client";
import type { SessionSummary } from "../types";

interface SessionBarProps {
  currentSessionId: string | null;
  onSelect: (session: SessionSummary) => void;
}

/** 顶栏会话选择：扫 data/checkpoints/ 的会话列表，可切换/删除。 */
export function SessionBar({ currentSessionId, onSelect }: SessionBarProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [open, setOpen] = useState(false);

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
        onClick={() => setOpen((prev) => !prev)}
        className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
      >
        会话 {currentSessionId ? `(${currentSessionId.slice(0, 8)})` : "（新建）"} ▾
      </button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 w-72 rounded-md border bg-white p-2 shadow-lg">
          <button
            onClick={() => {
              onSelect({} as SessionSummary); // 空对象 → 父组件视为"新会话"
              setOpen(false);
            }}
            className="w-full rounded px-2 py-1.5 text-left text-xs font-medium text-blue-600 hover:bg-blue-50"
          >
            ＋ 新会话
          </button>
          {sessions.map((session) => (
            <div
              key={session.session_id}
              className={`flex items-center gap-1 rounded px-2 py-1.5 text-xs hover:bg-slate-50 ${
                session.session_id === currentSessionId ? "bg-blue-50" : ""
              }`}
            >
              <button
                className="min-w-0 flex-1 text-left"
                onClick={() => {
                  onSelect(session);
                  setOpen(false);
                }}
                title={`${session.session_id} · ${session.updated_at}`}
              >
                <span className="font-mono text-[10px] text-slate-400">{session.session_id.slice(0, 8)}</span>
                <span className="ml-1.5 block truncate text-slate-700">{session.topic || "（无主题）"}</span>
              </button>
              <button
                className="shrink-0 text-slate-300 hover:text-red-500"
                title="删除会话"
                onClick={async () => {
                  if (!confirm(`删除会话 ${session.session_id.slice(0, 8)}？`)) return;
                  await deleteSession(session.session_id);
                  refresh();
                }}
              >
                ✕
              </button>
            </div>
          ))}
          {sessions.length === 0 && <p className="px-2 py-1.5 text-xs text-slate-400">暂无历史会话</p>}
        </div>
      )}
    </div>
  );
}
