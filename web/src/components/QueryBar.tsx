import { useRef, useState } from "react";

import { uploadPaper } from "../api/client";

interface QueryBarProps {
  onSubmit: (query: string, filePath: string | null) => void;
  disabled: boolean;
  /** 已上传待用的文件（paper_qa 模式） */
  uploadedFile: { path: string; name: string } | null;
  onUploaded: (file: { path: string; name: string } | null) => void;
}

/** 输入框 + PDF/MD/TXT 上传。带 file_path 提交时走 paper_qa 模式。 */
export function QueryBar({ onSubmit, disabled, uploadedFile, onUploaded }: QueryBarProps) {
  const [value, setValue] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const query = value.trim();
    if (query && !disabled) onSubmit(query, uploadedFile?.path ?? null);
  }

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const { file_path } = await uploadPaper(file);
      onUploaded({ path: file_path, name: file.name });
    } catch (err) {
      setError(String(err));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      {uploadedFile && (
        <div className="flex items-center gap-2 rounded-md bg-teal-50 px-3 py-1.5 text-xs text-teal-700">
          📄 {uploadedFile.name}
          <span className="text-teal-500">（将以 paper_qa 模式分析）</span>
          <button
            type="button"
            className="ml-auto text-teal-400 hover:text-teal-700"
            onClick={() => onUploaded(null)}
          >
            移除
          </button>
        </div>
      )}
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none disabled:bg-slate-100"
          placeholder="例如：调研 latent variable reasoning 的最新进展"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={disabled}
        />
        <label
          className={`flex cursor-pointer items-center rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-600 ${
            disabled ? "cursor-not-allowed opacity-50" : "hover:bg-slate-50"
          }`}
          title="上传 PDF / MD / TXT，对该论文提问"
        >
          {uploading ? "上传中…" : "＋ 论文"}
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.md,.txt"
            className="hidden"
            disabled={disabled}
            onChange={(event) => handleFile(event.target.files?.[0])}
          />
        </label>
        <button
          type="submit"
          disabled={disabled || uploading || !value.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {disabled ? "研究中…" : "开始研究"}
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </form>
  );
}
