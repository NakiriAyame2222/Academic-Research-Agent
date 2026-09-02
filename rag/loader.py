from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None

try:
    from langchain_community.document_loaders import TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover - fallback for environments without deps
    TextLoader = None
    RecursiveCharacterTextSplitter = None


SUPPORTED_TEXT_EXTENSIONS = {".md", ".txt"}
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS | {".pdf"}
SECTION_PATTERN = re.compile(r"^(#{1,6}\s+.+|\d+(?:\.\d+)*\s+.+|[A-Z][A-Za-z\s\-]{3,})$")



def _normalize_page(metadata: dict[str, Any]) -> int | None:
    page = metadata.get("page")
    if page is None:
        page = metadata.get("page_number")
    if page is None:
        return None
    try:
        return int(page) + 1 if int(page) >= 0 else int(page)
    except (TypeError, ValueError):
        return None



def _detect_section_title(text: str) -> str:
    for line in text.splitlines()[:12]:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 120:
            continue
        if SECTION_PATTERN.match(stripped):
            return stripped
    return ""



def _build_document(page_content: str, source: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = dict(metadata or {})
    path = Path(source)
    metadata.setdefault("source", str(path))
    metadata.setdefault("source_name", path.name)
    metadata.setdefault("source_type", path.suffix.lower().lstrip("."))
    metadata.setdefault("document_id", path.stem)
    metadata.setdefault("section_title", _detect_section_title(page_content))
    metadata["page"] = _normalize_page(metadata)
    return {
        "page_content": page_content,
        "metadata": metadata,
    }



def _load_pdf_with_pypdf(file_path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    reader = PdfReader(str(file_path))
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        documents.append(
            _build_document(
                text,
                str(file_path),
                {
                    "page": page_index,
                    "source_name": file_path.name,
                    "source_type": "pdf",
                    "document_id": file_path.stem,
                },
            )
        )
    return documents



def _load_pdf_with_fitz(file_path: Path) -> list[dict[str, Any]]:
    if fitz is None:
        return []
    documents: list[dict[str, Any]] = []
    with fitz.open(str(file_path)) as pdf:
        for page_index, page in enumerate(pdf):
            text = page.get_text("text") or ""
            if not text.strip():
                continue
            documents.append(
                _build_document(
                    text,
                    str(file_path),
                    {
                        "page": page_index,
                        "source_name": file_path.name,
                        "source_type": "pdf",
                        "document_id": file_path.stem,
                    },
                )
            )
    return documents



def _load_single_pdf(file_path: Path) -> list[dict[str, Any]]:
    documents = _load_pdf_with_pypdf(file_path)
    if documents:
        return documents
    return _load_pdf_with_fitz(file_path)



def _load_single_text(file_path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    if TextLoader is not None:
        loader = TextLoader(str(file_path), encoding="utf-8")
        for item in loader.load():
            metadata = dict(item.metadata)
            metadata.setdefault("document_id", file_path.stem)
            documents.append(_build_document(item.page_content, str(file_path), metadata))
        return documents

    documents.append(_build_document(file_path.read_text(encoding="utf-8", errors="ignore"), str(file_path), {"document_id": file_path.stem}))
    return documents



def _resolve_extra_metadata(
    path: Path,
    extra_metadata: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """按文件路径取额外元数据（如 arxiv_id / paper_title），兼容绝对与相对路径写法。"""
    if not extra_metadata:
        return {}
    candidates = [str(path), str(path.resolve()), path.name]
    for candidate in candidates:
        if candidate in extra_metadata:
            return dict(extra_metadata[candidate])
    normalized = {str(Path(key).resolve()): value for key, value in extra_metadata.items()}
    return dict(normalized.get(str(path.resolve()), {}))


def _load_path(path: Path, extra_metadata: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {path}")
    documents = _load_single_pdf(path) if suffix == ".pdf" else _load_single_text(path)

    injected = _resolve_extra_metadata(path, extra_metadata)
    if injected:
        for document in documents:
            document["metadata"].update(injected)
    return documents


def load_documents(
    data_dir: str | None = None,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if file_paths:
        documents: list[dict[str, Any]] = []
        for item in file_paths:
            path = Path(item)
            if not path.exists():
                raise FileNotFoundError(f"Document not found: {item}")
            documents.extend(_load_path(path, extra_metadata))
        return documents

    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {file_path}")
        return _load_path(path, extra_metadata)

    if not data_dir:
        return []

    documents: list[dict[str, Any]] = []
    root = Path(data_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        documents.extend(_load_path(path, extra_metadata))
    return documents



def split_documents(
    documents: list[dict[str, Any]],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[dict[str, Any]]:
    if not documents:
        return []

    split_docs: list[dict[str, Any]] = []
    separators = ["\n\\section", "\n## ", "\n### ", "\n\n", "\n", " "]
    if RecursiveCharacterTextSplitter is not None:
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap, separators=separators)
        for document in documents:
            chunks = splitter.split_text(document["page_content"])
            running_offset = 0
            for index, chunk in enumerate(chunks):
                metadata = dict(document.get("metadata", {}))
                metadata["chunk_index"] = index
                metadata["char_start"] = running_offset
                metadata["char_end"] = running_offset + len(chunk)
                metadata["section_title"] = metadata.get("section_title") or _detect_section_title(chunk)
                running_offset += len(chunk)
                split_docs.append(_build_document(chunk, metadata.get("source", "unknown"), metadata))
        return split_docs

    step = max(1, chunk_size - chunk_overlap)
    for document in documents:
        text = document["page_content"]
        metadata = dict(document.get("metadata", {}))
        source = metadata.get("source", "unknown")
        for index, start in enumerate(range(0, len(text), step)):
            chunk = text[start : start + chunk_size]
            chunk_metadata = dict(metadata)
            chunk_metadata["chunk_index"] = index
            chunk_metadata["char_start"] = start
            chunk_metadata["char_end"] = start + len(chunk)
            chunk_metadata["section_title"] = chunk_metadata.get("section_title") or _detect_section_title(chunk)
            split_docs.append(_build_document(chunk, source, chunk_metadata))
    return split_docs



def prepare_documents(
    data_dir: str | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 80,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    documents = load_documents(
        data_dir=data_dir,
        file_path=file_path,
        file_paths=file_paths,
        extra_metadata=extra_metadata,
    )
    return split_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
