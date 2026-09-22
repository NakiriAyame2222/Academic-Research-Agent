import { useEffect, useState } from "react";

import { listPendingMemories, saveMemories } from "../api/client";

interface MemoryConfirmProps {
  sessionId: string;
  /** 保存完成（含 0 条）后回调，父组件据此收起面板 */
  onDone: () => void;
}

/** 待确认长期记忆：勾选后保存进 SQLite（对应 CLI 的交互式确认）。 */
export function MemoryConfirm({ sessionId, onDone }: MemoryConfirmProps) {
  const [candidates, setCandidates] = useState<
    { index: number; content: string; memory_type: string }[] | null
  >(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    listPendingMemories(sessionId)
      .then((data) => setCandidates(data.candidates))
      .catch((err) => {
        setCandidates([]);
        setError(String(err));
      });
  }, [sessionId]);

  if (candidates === null) return <p className="text-xs text-slate-400">加载待确认记忆…</p>;
  if (candidates.length === 0) return null;

  function toggle(index: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    try {
      await saveMemories(sessionId, [...selected]);
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-lg border border-pink-200 bg-pink-50/50 p-4">
      <h2 className="mb-2 text-sm font-semibold">待确认的长期记忆</h2>
      <ul className="space-y-1.5">
        {candidates.map((candidate) => (
          <li key={candidate.index} className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={selected.has(candidate.index)}
              onChange={() => toggle(candidate.index)}
            />
            <div>
              <span className="mr-1.5 rounded bg-pink-100 px-1.5 py-0.5 text-[10px] text-pink-700">
                {candidate.memory_type}
              </span>
              {candidate.content}
            </div>
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-xs text-red-600">保存失败：{error}</p>}
      <div className="mt-3 flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-pink-600 px-3 py-1.5 text-xs font-medium text-white disabled:bg-slate-400"
        >
          {saving ? "保存中…" : `保存勾选的 ${selected.size} 条`}
        </button>
        <button
          onClick={onDone}
          disabled={saving}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-600"
        >
          本轮不保存
        </button>
      </div>
    </section>
  );
}
