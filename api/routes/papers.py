"""PDF 上传与本地论文列表：复用 mcp_server 的 data_dir 越权校验模式。"""
from __future__ import annotations

import math
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from agent.intent import SUPPORTED_PAPER_EXTENSIONS
from api.schemas import PaperInfo
from config import get_settings
from memory.short_term import storage_path
from storage import Database
from rag.loader import prepare_documents

router = APIRouter(prefix="/api/papers", tags=["papers"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def _safe_upload_name(filename: str) -> str:
    """与 tools/arxiv_search.py:_safe_name 同样的清洗，但保留原扩展名（后者硬编码 .pdf）。

    非 [字母数字_] 全部替换为 _，路径分隔符无法存活；附内容无关的短哈希防重名。
    """
    import hashlib

    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()[:10]
    stem = "".join(ch if ch.isalnum() else "_" for ch in Path(filename).stem)[:40].strip("_") or "upload"
    suffix = Path(filename).suffix.lower()
    return f"{stem}_{digest}{suffix}"


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    exponent = min(int(math.log(size_bytes, 1024)), len(units) - 1)
    return f"{size_bytes / 1024 ** exponent:.1f} {units[exponent]}"


@router.post("/upload")
async def upload_paper(file: UploadFile) -> dict:
    """上传 PDF/MD/TXT 到 data/papers/，返回落盘的 file_path（可直接作为 research 的 file_path）。

    校验（防路径穿越）：
    - 扩展名白名单（agent/intent.py 的 SUPPORTED_PAPER_EXTENSIONS）
    - 文件名经 _safe_name 规范化（tools/arxiv_search.py），杜绝路径穿越/非法字符
    - 大小上限 50MB
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_PAPER_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型 {suffix}，仅支持 {sorted(SUPPORTED_PAPER_EXTENSIONS)}")

    settings = get_settings()
    papers_dir = Path(settings["papers_dir"])
    papers_dir.mkdir(parents=True, exist_ok=True)
    # 安全名清洗后目录分隔符无法存活，最终路径必在 papers_dir 内
    safe_path = papers_dir / _safe_upload_name(file.filename)

    written = 0
    try:
        with safe_path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"文件超过上限 {_human_size(MAX_UPLOAD_BYTES)}")
                target.write(chunk)
    except HTTPException:
        safe_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    digest = hashlib.sha256(safe_path.read_bytes()).hexdigest()
    relative_path = str(safe_path.resolve().relative_to(Path(settings["data_dir"]).resolve())).replace("\\", "/")
    with Database(storage_path()) as db:
        with db.transaction(immediate=True):
            document = db.repositories.documents.upsert(
                digest,
                source_uri=relative_path,
                local_path=relative_path,
                title=safe_path.stem,
                mime_type=file.content_type or "application/octet-stream",
                usage_type="user",
                keep_local=True,
                metadata={"filename": safe_path.name},
            )
            for chunk in prepare_documents(file_path=str(safe_path), chunk_size=settings["chunk_size"], chunk_overlap=settings["chunk_overlap"]):
                metadata = dict(chunk.get("metadata") or {})
                db.repositories.document_chunks.upsert(
                    document["id"],
                    int(metadata.get("chunk_index") or 0),
                    str(chunk.get("page_content") or ""),
                    page=metadata.get("page"),
                    section_title=str(metadata.get("section_title") or ""),
                    metadata=metadata,
                )
    return {"document_id": document["id"], "file_path": str(safe_path), "size_bytes": written, "file_name": safe_path.name, "vector_status": "pending"}


@router.get("", response_model=list[PaperInfo])
def list_papers() -> list[PaperInfo]:
    """列出 data/papers 下可检索的论文文件（复用 mcp_server.list_local_papers 的逻辑）。"""
    settings = get_settings()
    papers_dir = Path(settings["papers_dir"])
    if not papers_dir.exists():
        return []
    results: list[PaperInfo] = []
    for path in sorted(papers_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_PAPER_EXTENSIONS:
            relative_path = str(path.resolve().relative_to(Path(settings["data_dir"]).resolve())).replace("\\", "/")
            with Database(storage_path()) as db:
                document = db.repositories.documents.get_by_sha256(hashlib.sha256(path.read_bytes()).hexdigest())
            results.append(
                PaperInfo(
                    document_id=str((document or {}).get("id", "")),
                    file_name=path.name,
                    file_path=str(path),
                    size_bytes=path.stat().st_size,
                    suffix=path.suffix.lower(),
                    file_url=f"/api/papers/file?path={relative_path}",
                )
            )
    return results


@router.get("/file")
def download_paper(path: str):
    """Serve only files below the formal user-files root."""
    from fastapi.responses import FileResponse

    settings = get_settings()
    root = Path(settings["user_files_dir"]).resolve()
    resolved = (Path(settings["data_dir"]) / path).resolve()
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.lower() not in SUPPORTED_PAPER_EXTENSIONS:
        raise HTTPException(status_code=404, detail="paper not found")
    return FileResponse(resolved, filename=resolved.name)
