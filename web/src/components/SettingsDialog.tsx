import { useEffect, useState } from "react";

import { getSettings, updateSettings, type ModelSettings } from "../api/client";

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
export function SettingsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<ModelSettings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSettings()
      .then((data) => setForm({ ...data, llm_api_key: "", embedding_api_key: "" }))
      .catch((err) => setError(String(err)));
  }, []);

  if (!form) {
    return (
      <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/30" onClick={onClose}>
        <div className="rounded-lg bg-white p-5 text-sm text-slate-500" onClick={(e) => e.stopPropagation()}>
          加载配置…
        </div>
      </div>
    );
  }

  async function handleSave() {
    if (!form) return;
    setSaving(true);
    setError("");
    try {
      await updateSettings(form);
      onSaved();
      onClose();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="w-[26rem] rounded-lg bg-white p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="mb-1 text-sm font-semibold">模型配置</h2>
        <p className="mb-4 text-xs text-slate-400">
          写入 data/llm_config.env 持久化，保存后立即生效（与 CLI 的 --save-config 同一入口）。
        </p>
        <div className="space-y-3">
          {FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">{field.label}</span>
              <input
                type={field.key.endsWith("api_key") ? "password" : "text"}
                className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-xs focus:border-slate-500 focus:outline-none"
                placeholder={field.placeholder}
                value={form[field.key]}
                onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
              />
            </label>
          ))}

          <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
            <p className="mb-2 text-xs font-medium text-slate-600">运行参数</p>
            <div className="grid grid-cols-2 gap-3">
              {NUMERIC_FIELDS.map((field) => (
                <label key={field.key} className="block">
                  <span className="mb-1 block text-xs text-slate-500" title={field.hint}>
                    {field.label}
                  </span>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-xs focus:border-slate-500 focus:outline-none"
                    value={form[field.key]}
                    onChange={(event) =>
                      setForm({ ...form, [field.key]: Number(event.target.value) || 0 })
                    }
                  />
                  <span className="mt-0.5 block text-[10px] text-slate-400">{field.hint}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
        {error && <p className="mt-3 text-xs text-red-600">保存失败：{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-600">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:bg-slate-400"
          >
            {saving ? "保存中…" : "保存并生效"}
          </button>
        </div>
      </div>
    </div>
  );
}
