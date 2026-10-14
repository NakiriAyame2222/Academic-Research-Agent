import { useEffect, useRef, useState } from "react";

import { listPapers, uploadPaper, type PaperInfo, type RetrievalOptions, type SelectedFile } from "../api/client";

interface QueryBarProps {
  onSubmit: (query: string, filePath: string | null) => void;
  disabled?: boolean;
  submitting?: boolean;
  selectedFile: SelectedFile | null;
  onFileSelected: (file: SelectedFile | null) => void;
  retrievalOptions: RetrievalOptions;
  onRetrievalOptionsChange: (options: RetrievalOptions) => void;
  placeholder?: string;
  buttonText?: string;
  commandMode?: boolean;
}

/** 研究输入、上传与已有论文选择；运行中切换为主题控制指令。 */
export function QueryBar({
  onSubmit,
  disabled = false,
  submitting = false,
  selectedFile,
  onFileSelected,
  retrievalOptions,
  onRetrievalOptionsChange,
  placeholder,
  buttonText,
  commandMode = false,
}: QueryBarProps) {
  const [value, setValue] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [papers, setPapers] = useState<PaperInfo[]>([]);
  const [loadingPapers, setLoadingPapers] = useState(false);
  const [libraryError, setLibraryError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = disabled || submitting || uploading;
  const filesDisabled = busy || commandMode;
  const fileRequest = useRef(0);
  useEffect(() => () => { fileRequest.current += 1; }, []);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const query = value.trim();
    if (query && !busy) {
      onSubmit(query, selectedFile?.path ?? null);
      setValue("");
    }
  }

  async function refreshPapers() {
    setLoadingPapers(true);
    setLibraryError("");
    try {
      setPapers(await listPapers());
    } catch (err) {
      setLibraryError(`读取论文库失败：${String(err)}`);
    } finally {
      setLoadingPapers(false);
    }
  }

  async function handleFile(file: File | undefined) {
    if (!file || filesDisabled) return;
    const request = ++fileRequest.current;
    setUploading(true);
    setError("");
    try {
      const { file_path } = await uploadPaper(file);
      if (request === fileRequest.current) {
        onFileSelected({ path: file_path, name: file.name });
        setLibraryOpen(false);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2.5">
      {selectedFile && (
        <div className="flex items-center gap-2 rounded-md bg-accent-soft px-3 py-1.5 text-xs text-ink">
          <span className="min-w-0 truncate">📄 {selectedFile.name}</span>
          <span className="shrink-0 text-ink-muted">（围绕这篇论文回答）</span>
          <button
            type="button"
            disabled={filesDisabled}
            className="ml-auto rounded px-1.5 py-0.5 text-xs text-ink-muted transition hover:bg-surface hover:text-ink disabled:opacity-40"
            onClick={() => onFileSelected(null)}
          >
            移除
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <input
          aria-label={commandMode ? "研究控制指令" : "研究问题"}
          className="min-w-0 flex-1 rounded-md border border-rule bg-surface px-3.5 py-2.5 font-sans text-sm text-ink placeholder:font-sans placeholder:text-ink-muted/60 focus:border-accent focus:outline-none disabled:bg-paper disabled:text-ink-muted"
          placeholder={placeholder || (selectedFile ? "例如：这篇论文的方法和实验有哪些局限？" : "例如：调研 latent variable reasoning 的最新进展")}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
        />
        <label
          className={`flex items-center rounded-md border border-rule px-3.5 py-2.5 font-sans text-xs text-ink-muted transition ${
            filesDisabled ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-paper hover:text-ink"
          }`}
          title="上传 PDF / MD / TXT，对该论文提问"
        >
          {uploading ? "上传中…" : "＋ 上传论文"}
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.md,.txt"
            className="hidden"
            disabled={filesDisabled}
            onChange={(event) => handleFile(event.target.files?.[0])}
          />
        </label>
        <button
          type="button"
          disabled={filesDisabled}
          aria-expanded={libraryOpen && !commandMode}
          onClick={() => {
            setLibraryOpen(!libraryOpen);
            if (!libraryOpen) void refreshPapers();
          }}
          className="rounded-md border border-rule px-3 py-2.5 text-xs text-ink-muted transition hover:bg-paper hover:text-ink disabled:opacity-40"
        >
          已有论文
        </button>
        <button
          type="submit"
          disabled={busy || !value.trim()}
          className="rounded-md bg-accent px-5 py-2.5 font-sans text-sm font-medium text-white transition hover:bg-[#203a6b] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {buttonText || "开始研究"}
        </button>
      </div>

      {libraryOpen && !commandMode && (
        <div className="space-y-2 rounded-md border border-rule bg-paper p-3">
          <div className="flex items-center justify-between text-xs">
            <label htmlFor="existing-paper">选择已有论文</label>
            <button type="button" disabled={filesDisabled || loadingPapers} onClick={() => void refreshPapers()} className="text-accent disabled:opacity-40">
              {loadingPapers ? "读取中…" : "刷新列表"}
            </button>
          </div>
          <select
            id="existing-paper"
            value={papers.some((p) => p.file_path === selectedFile?.path) ? selectedFile!.path : ""}
            disabled={filesDisabled || loadingPapers}
            className="w-full rounded-md border border-rule bg-surface px-2 py-2 text-xs"
            onChange={(event) => {
              const paper = papers.find((p) => p.file_path === event.target.value);
              if (paper) {
                onFileSelected({ path: paper.file_path, name: paper.file_name });
                setLibraryOpen(false);
              }
            }}
          >
            <option value="">请选择论文…</option>
            {papers.map((paper) => (
              <option key={paper.file_path} value={paper.file_path}>
                {paper.file_name}（{Math.max(1, Math.round(paper.size_bytes / 1024))} KB）
              </option>
            ))}
          </select>
          {!loadingPapers && !libraryError && !papers.length && <p className="text-xs text-ink-muted">论文库为空，请先上传 PDF、MD 或 TXT 文件。</p>}
          {libraryError && <p role="alert" className="text-xs text-danger">{libraryError}</p>}
        </div>
      )}

      {!commandMode && (
        <details className="text-xs text-ink-muted">
          <summary className="cursor-pointer py-1">本次检索设置</summary>
          <div className="mt-2 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2">
              检索数量
              <input
                aria-label="检索数量"
                type="number"
                min={1}
                max={20}
                step={1}
                placeholder="默认"
                value={retrievalOptions.top_k ?? ""}
                disabled={busy}
                onChange={(event) => onRetrievalOptionsChange({
                  ...retrievalOptions,
                  top_k: event.target.value === "" ? undefined : Number(event.target.value),
                })}
                className="w-20 rounded border border-rule bg-surface px-2 py-1 text-ink"
              />
            </label>
            {selectedFile && (
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={retrievalOptions.rebuild_index}
                  disabled={busy}
                  onChange={(event) => onRetrievalOptionsChange({ ...retrievalOptions, rebuild_index: event.target.checked })}
                />
                本轮开始前重建这篇论文的索引
              </label>
            )}
          </div>
          <p className="mt-2">数量留空时使用「研究设置」中的默认值。论文内容更新后可重建索引，成功提交后该选项自动取消。</p>
        </details>
      )}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </form>
  );
}
