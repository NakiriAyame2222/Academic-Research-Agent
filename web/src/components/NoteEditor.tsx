import { useState } from "react";
import { saveResearchNote } from "../api/client";

export function NoteEditor({ sessionId, report, onDone }: {
  sessionId: string; report: string; onDone: () => void;
}) {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await saveResearchNote(sessionId, content.trim());
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-lg border border-rule bg-surface p-4 font-sans">
      <label htmlFor="research-note" className="mb-2 block text-sm font-medium text-ink">保存研究笔记</label>
      <p className="mb-2 text-xs text-ink-muted">选择需要长期保留的结论或补充自己的笔记，后续研究可检索使用。</p>
      <textarea id="research-note" value={content} onChange={(e) => setContent(e.target.value)}
        maxLength={20000} rows={5} disabled={saving}
        className="w-full rounded border border-rule bg-paper p-2 text-sm text-ink"
        placeholder="输入要保存的内容…" />
      {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <button type="button" disabled={saving || !report || report.length > 20000}
          title={report.length > 20000 ? "报告超过笔记长度限制，请选取需要的内容" : undefined}
          onClick={() => setContent(report)} className="rounded border border-rule px-3 py-1.5 disabled:opacity-40">填入本轮报告</button>
        <button type="button" onClick={save} disabled={saving || !content.trim()}
          className="rounded bg-accent px-3 py-1.5 text-white disabled:opacity-40">{saving ? "保存中…" : "保存笔记"}</button>
        <button type="button" onClick={onDone} disabled={saving} className="px-3 py-1.5 text-ink-muted">关闭</button>
      </div>
    </section>
  );
}
