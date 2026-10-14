import { useEffect, useState } from "react";

import { getSettings, updateSettings, getOutputPreferences, saveOutputPreferences, type ModelSettings, type OutputPreferences } from "../api/client";

const FIELDS: { key: keyof ModelSettings; label: string; placeholder: string }[] = [
  { key: "llm_model", label: "LLM 模型", placeholder: "例如 gpt-4o-mini" },
  { key: "llm_base_url", label: "LLM Base URL", placeholder: "例如 https://api.example.com/v1（留空用官方）" },
  { key: "llm_api_key", label: "LLM API Key", placeholder: "已保存（掩码显示）；留空不修改" },
  { key: "embedding_model", label: "Embedding 模型", placeholder: "例如 text-embedding-3-small" },
  { key: "embedding_base_url", label: "Embedding Base URL", placeholder: "留空沿用 LLM Base URL" },
  { key: "embedding_api_key", label: "Embedding API Key", placeholder: "已保存（掩码显示）；留空不修改" },
];

const NUMERIC_FIELDS: {
  key: "max_research_rounds" | "default_top_k";
  label: string;
  min: number;
  max: number;
  hint: string;
}[] = [
  {
    key: "max_research_rounds",
    label: "最大研究轮次",
    min: 1,
    max: 5,
    hint: "证据不足时最多回头补证几轮（反思回边）",
  },
  {
    key: "default_top_k",
    label: "每轮论文数（top_k）",
    min: 1,
    max: 10,
    hint: "每次检索的返回条数；API 有 RPM 限制时调低",
  },
];

/** 模型配置弹层：Web 端的 --model/--api-key/--base-url + --save-config。 */
export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState<ModelSettings | null>(null);
  const [preferences, setPreferences] = useState<OutputPreferences | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([getSettings(), getOutputPreferences()])
      .then(([data, prefs]) => {
        setForm({ ...data, llm_api_key: "", embedding_api_key: "" });
        setPreferences(prefs);
      })
      .catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  if (!form || !preferences) {
    return (
      <div
        className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 backdrop-blur-sm"
        onClick={onClose}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-dialog-title"
      >
        <div
          className="max-h-[90vh] w-[30rem] max-w-[95vw] overflow-y-auto rounded-xl border border-rule bg-surface p-6 shadow-xl font-sans"
          onClick={(e) => e.stopPropagation()}
        >
          {error ? (
            <div>
              <h2 id="settings-dialog-title" className="mb-2 text-sm font-medium text-ink">
                加载配置失败
              </h2>
              <p className="mb-4 text-xs font-medium text-danger">{error}</p>
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-rule px-3 py-1.5 text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
                >
                  关闭
                </button>
              </div>
            </div>
          ) : (
            <div id="settings-dialog-title" className="text-sm text-ink-muted">
              加载配置…
            </div>
          )}
        </div>
      </div>
    );
  }

  async function handleSave() {
    if (!form || !preferences) return;
    setSaving(true);
    setError("");
    try {
      await updateSettings(form);
      await saveOutputPreferences(preferences);
      onClose();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-dialog-title"
    >
      <div
        className="max-h-[90vh] w-[30rem] max-w-[95vw] overflow-y-auto rounded-xl border border-rule bg-surface p-6 shadow-xl font-sans"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="settings-dialog-title" className="mb-1 text-sm font-medium text-ink">研究设置</h2>
        <p className="mb-4 text-xs text-ink-muted">
          保存后用于后续研究；本轮问题中的明确要求优先。
        </p>
        <div className="space-y-3">
          <div className="rounded-lg border border-rule bg-paper p-3.5">
            <p className="mb-2 text-xs font-medium text-ink">默认输出偏好</p>
            <div className="grid grid-cols-2 gap-3">
              {([
                ["language", "语言", [["Chinese", "中文"], ["English", "英文"]]],
                ["format", "格式", [["structured", "结构化报告"], ["table", "比较表格"], ["markdown", "Markdown"], ["bullet", "要点列表"], ["narrative", "连贯叙述"]]],
                ["tone", "风格", [["academic", "学术"], ["concise", "简洁"], ["neutral", "中性"], ["tutorial", "讲解"]]],
                ["depth", "深度", [["overview", "概览"], ["standard", "标准"], ["deep", "深入"]]],
              ] as const).map(([key, label, options]) => (
                <label key={key} className="block text-xs text-ink-muted">
                  {label}
                  <select value={preferences[key]}
                    onChange={(event) => setPreferences({ ...preferences, [key]: event.target.value })}
                    className="mt-1 w-full rounded border border-rule bg-surface px-2 py-1.5 text-ink">
                    {options.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
                  </select>
                </label>
              ))}
            </div>
          </div>
          {FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-xs font-medium text-ink-muted">{field.label}</span>
              <input
                type={field.key.endsWith("api_key") ? "password" : "text"}
                className="w-full rounded-md border border-rule bg-surface px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-muted/50 focus:border-accent focus:outline-none"
                placeholder={field.placeholder}
                value={form[field.key]}
                onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
              />
            </label>
          ))}

          <div className="rounded-lg border border-rule bg-paper p-3.5">
            <p className="mb-2 text-xs font-medium text-ink">运行参数</p>
            <div className="grid grid-cols-2 gap-3">
              {NUMERIC_FIELDS.map((field) => (
                <label key={field.key} className="block">
                  <span className="mb-1 block text-xs text-ink-muted" title={field.hint}>
                    {field.label}
                  </span>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    className="w-full rounded-md border border-rule bg-surface px-2.5 py-1.5 text-xs text-ink focus:border-accent focus:outline-none"
                    value={form[field.key]}
                    onChange={(event) =>
                      setForm({ ...form, [field.key]: Number(event.target.value) || 0 })
                    }
                  />
                  <span className="mt-0.5 block text-[10px] text-ink-muted/70">{field.hint}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
        {error && <p className="mt-3 text-xs font-medium text-danger">保存失败：{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md border border-rule px-3 py-1.5 text-xs text-ink-muted transition hover:bg-paper hover:text-ink"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-[#203a6b] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? "保存中…" : "保存并生效"}
          </button>
        </div>
      </div>
    </div>
  );
}
