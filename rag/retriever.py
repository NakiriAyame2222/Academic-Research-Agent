from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from config import get_embedder, get_settings
from rag.loader import prepare_documents
from rag.sparse import (
    DENSE_WEIGHT,
    SPARSE_WEIGHT,
    build_query_sparse_vector,
    build_sparse_index,
    cosine_similarity,
    sparse_cosine_similarity,
)
from rag.text_tokenizer import TOKENIZER_VERSION, tokenize

_VECTOR_STORE_CACHE: dict[str, Any] = {}
_BACKEND_NOTICES: list[str] = []


def _tokenize(text: str) -> list[str]:
    """保留原函数名以兼容既有调用；实现改为共享的 CJK 感知分词。"""
    return tokenize(text)


def take_backend_notices() -> list[str]:
    notices = list(_BACKEND_NOTICES)
    _BACKEND_NOTICES.clear()
    return notices


class SimpleRetriever:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]] | None = None,
        sparse_vectors: list[dict[str, float]] | None = None,
        idf: dict[str, float] | None = None,
        top_k: int = 4,
    ) -> None:
        self.documents = documents
        self.embeddings = embeddings or []
        self.sparse_vectors = sparse_vectors or []
        self.idf = idf or {}
        self.top_k = top_k

    def invoke(self, query: str) -> list[dict[str, Any]]:
        if not self.documents:
            return []

        embedder = get_embedder()
        query_embedding = None
        if embedder is not None and self.embeddings:
            try:
                query_embedding = embedder.embed_query(query)
            except Exception:
                query_embedding = None

        query_sparse = build_query_sparse_vector(query, self.idf)
        scored_docs: list[tuple[float, dict[str, Any]]] = []
        for index, document in enumerate(self.documents):
            semantic_score = 0.0
            if query_embedding is not None and index < len(self.embeddings):
                semantic_score = cosine_similarity(query_embedding, self.embeddings[index])

            sparse_score = 0.0
            if index < len(self.sparse_vectors):
                sparse_score = sparse_cosine_similarity(query_sparse, self.sparse_vectors[index])

            combined = (
                semantic_score * DENSE_WEIGHT + sparse_score * SPARSE_WEIGHT
                if query_embedding is not None
                else sparse_score
            )
            if combined > 0:
                scored_docs.append((combined, document))

        if not scored_docs:
            return self.documents[: self.top_k]

        scored_docs.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored_docs[: self.top_k]]


class SimpleVectorStore:
    backend = "simple"

    def __init__(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]] | None = None,
        sparse_vectors: list[dict[str, float]] | None = None,
        idf: dict[str, float] | None = None,
        persist_dir: str | None = None,
    ) -> None:
        self.documents = documents
        self.embeddings = embeddings or []
        self.sparse_vectors = sparse_vectors or []
        self.idf = idf or {}
        self.persist_dir = persist_dir

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> SimpleRetriever:
        search_kwargs = search_kwargs or {}
        top_k = int(search_kwargs.get("k", 4))
        return SimpleRetriever(
            self.documents,
            embeddings=self.embeddings,
            sparse_vectors=self.sparse_vectors,
            idf=self.idf,
            top_k=top_k,
        )

    def persist(self) -> None:
        if not self.persist_dir:
            return
        persist_path = Path(self.persist_dir)
        persist_path.mkdir(parents=True, exist_ok=True)
        (persist_path / "documents.json").write_text(json.dumps(self.documents, ensure_ascii=False, indent=2), encoding="utf-8")
        (persist_path / "embeddings.json").write_text(json.dumps(self.embeddings, ensure_ascii=False), encoding="utf-8")
        (persist_path / "sparse_vectors.json").write_text(json.dumps(self.sparse_vectors, ensure_ascii=False), encoding="utf-8")
        (persist_path / "idf.json").write_text(json.dumps(self.idf, ensure_ascii=False), encoding="utf-8")


def _normalize_persist_dir(persist_dir: str | Path) -> str:
    return str(Path(persist_dir))


def get_file_scope_dir(base_dir: str | Path, file_path: str) -> Path:
    normalized_path = str(Path(file_path).resolve())
    digest = hashlib.md5(normalized_path.encode("utf-8")).hexdigest()
    return Path(base_dir) / digest


def get_session_scope_dir(base_dir: str | Path, session_id: str, scope_name: str) -> Path:
    safe_session = hashlib.md5(session_id.encode("utf-8")).hexdigest()[:12]
    safe_scope = re.sub(r"[^a-zA-Z0-9_-]+", "_", scope_name).strip("_") or "scope"
    return Path(base_dir) / "sessions" / safe_session / safe_scope


def clear_vector_store_cache(persist_dir: str | None = None) -> None:
    if persist_dir is None:
        _VECTOR_STORE_CACHE.clear()
        return
    _VECTOR_STORE_CACHE.pop(_normalize_persist_dir(persist_dir), None)


def _build_embeddings(documents: list[dict[str, Any]]) -> list[list[float]]:
    embedder = get_embedder()
    if embedder is None or not documents:
        return []
    texts = [doc.get("page_content", "") for doc in documents]
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        # 早期这里静默返回 []，导致 embedding 一失败就整库退化成纯稀疏检索，
        # 且没有任何痕迹。现在把失败原因暴露出来（会被 emit_progress 展示）。
        _BACKEND_NOTICES.append(f"文档向量化失败，本次检索退化为纯稀疏：{type(exc).__name__}: {exc}"[:200])
        return []
    if len(vectors) != len(documents):
        _BACKEND_NOTICES.append(f"向量化返回数量不匹配（{len(vectors)}/{len(documents)}），退化为纯稀疏")
        return []
    return vectors


def _resolve_backend() -> str:
    backend = str(get_settings().get("vector_backend") or "auto").lower()
    return backend if backend in {"auto", "chroma", "simple"} else "auto"


def _index_meta(backend: str, document_count: int) -> dict[str, Any]:
    settings = get_settings()
    return {
        "backend": backend,
        "tokenizer_version": TOKENIZER_VERSION,
        "embedding_model": str(settings.get("embedding_model") or ""),
        "document_count": document_count,
        "dense_weight": DENSE_WEIGHT,
        "sparse_weight": SPARSE_WEIGHT,
    }


def _write_index_meta(persist_dir: str, backend: str, document_count: int) -> None:
    persist_path = Path(persist_dir)
    persist_path.mkdir(parents=True, exist_ok=True)
    (persist_path / "meta.json").write_text(
        json.dumps(_index_meta(backend, document_count), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _meta_is_compatible(persist_dir: str, document_count: int) -> bool:
    """分词实现或 embedding 模型变了就必须重建，否则新旧产物语义不一致。"""
    meta_file = Path(persist_dir) / "meta.json"
    if not meta_file.exists():
        return False
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = _index_meta(str(meta.get("backend") or "simple"), document_count)
    return meta.get("tokenizer_version") == expected["tokenizer_version"] and meta.get("embedding_model") == expected["embedding_model"]


def build_vector_store(documents: list[dict[str, Any]], persist_dir: str | None = None) -> Any:
    normalized_dir = _normalize_persist_dir(persist_dir) if persist_dir else None
    embeddings = _build_embeddings(documents)
    sparse_vectors, idf = build_sparse_index(documents)

    backend = _resolve_backend()
    store: Any = None
    if backend in {"auto", "chroma"} and normalized_dir and documents:
        store = _try_build_chroma(documents, normalized_dir, embeddings, sparse_vectors, idf, strict=backend == "chroma")

    if store is None:
        store = SimpleVectorStore(
            documents,
            embeddings=embeddings,
            sparse_vectors=sparse_vectors,
            idf=idf,
            persist_dir=normalized_dir,
        )

    store.persist()
    if normalized_dir:
        _write_index_meta(normalized_dir, getattr(store, "backend", "simple"), len(documents))
        _VECTOR_STORE_CACHE[normalized_dir] = store
    return store


def _try_build_chroma(
    documents: list[dict[str, Any]],
    persist_dir: str,
    embeddings: list[list[float]],
    sparse_vectors: list[dict[str, float]],
    idf: dict[str, float],
    strict: bool = False,
) -> Any | None:
    from rag.chroma_store import ChromaUnavailable, ChromaVectorStore, chroma_available

    if not chroma_available():
        _BACKEND_NOTICES.append("未安装 chromadb，向量检索降级为内置实现（pip install chromadb 可启用）")
        if strict:
            raise RuntimeError("RESEARCH_AGENT_VECTOR_BACKEND=chroma 但 chromadb 未安装")
        return None
    if not embeddings:
        _BACKEND_NOTICES.append("embedding 服务不可用，Chroma 后端缺少向量，降级为内置稀疏检索")
        return None
    try:
        store = ChromaVectorStore.create(documents, persist_dir, embeddings, sparse_vectors=sparse_vectors, idf=idf)
        _BACKEND_NOTICES.append(f"向量检索使用 Chroma 后端，已索引 {len(documents)} 个片段")
        return store
    except ChromaUnavailable as exc:
        _BACKEND_NOTICES.append(f"Chroma 初始化失败，降级为内置实现：{exc}")
        if strict:
            raise
        return None


def load_vector_store(persist_dir: str | Path) -> Any | None:
    normalized_dir = _normalize_persist_dir(persist_dir)
    if normalized_dir in _VECTOR_STORE_CACHE:
        return _VECTOR_STORE_CACHE[normalized_dir]

    documents_file = Path(normalized_dir) / "documents.json"
    if not documents_file.exists():
        return None

    documents = json.loads(documents_file.read_text(encoding="utf-8"))
    if not _meta_is_compatible(normalized_dir, len(documents)):
        _BACKEND_NOTICES.append("检索索引的分词/embedding 版本已变化，正在重建索引")
        return None

    embeddings_file = Path(normalized_dir) / "embeddings.json"
    sparse_vectors_file = Path(normalized_dir) / "sparse_vectors.json"
    idf_file = Path(normalized_dir) / "idf.json"
    embeddings = json.loads(embeddings_file.read_text(encoding="utf-8")) if embeddings_file.exists() else []
    sparse_vectors = json.loads(sparse_vectors_file.read_text(encoding="utf-8")) if sparse_vectors_file.exists() else []
    idf = json.loads(idf_file.read_text(encoding="utf-8")) if idf_file.exists() else {}

    store: Any | None = None
    if _resolve_backend() in {"auto", "chroma"}:
        store = _try_load_chroma(documents, normalized_dir, sparse_vectors, idf)

    if store is None:
        store = SimpleVectorStore(
            documents, embeddings=embeddings, sparse_vectors=sparse_vectors, idf=idf, persist_dir=normalized_dir
        )
    _VECTOR_STORE_CACHE[normalized_dir] = store
    return store


def _try_load_chroma(
    documents: list[dict[str, Any]],
    persist_dir: str,
    sparse_vectors: list[dict[str, float]],
    idf: dict[str, float],
) -> Any | None:
    try:
        from rag.chroma_store import ChromaUnavailable, ChromaVectorStore, chroma_available
    except ImportError:  # pragma: no cover
        return None
    if not chroma_available() or not (Path(persist_dir) / "chroma").exists():
        return None
    try:
        return ChromaVectorStore.load(documents, persist_dir, sparse_vectors=sparse_vectors, idf=idf)
    except ChromaUnavailable:
        return None


def rebuild_vector_store(
    data_dir: str | None,
    persist_dir: str | Path,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
) -> Any:
    normalized_dir = _normalize_persist_dir(persist_dir)
    clear_vector_store_cache(normalized_dir)
    documents = prepare_documents(
        data_dir=data_dir,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        file_path=file_path,
        file_paths=file_paths,
        extra_metadata=extra_metadata,
    )
    return build_vector_store(documents, persist_dir=normalized_dir)


def initialize_retriever(
    data_dir: str | None,
    persist_dir: str | Path,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    rebuild: bool = False,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
) -> Any:
    normalized_dir = _normalize_persist_dir(persist_dir)
    if rebuild:
        return rebuild_vector_store(
            data_dir=data_dir,
            persist_dir=normalized_dir,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            file_path=file_path,
            file_paths=file_paths,
            extra_metadata=extra_metadata,
        )

    store = load_vector_store(normalized_dir)
    if store is not None and store.documents:
        return store

    return rebuild_vector_store(
        data_dir=data_dir,
        persist_dir=normalized_dir,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        file_path=file_path,
        file_paths=file_paths,
        extra_metadata=extra_metadata,
    )


def get_retriever(top_k: int = 4, persist_dir: str | Path | None = None) -> Any:
    if persist_dir is None:
        raise ValueError("persist_dir is required")
    normalized_dir = _normalize_persist_dir(persist_dir)
    if normalized_dir in _VECTOR_STORE_CACHE:
        return _VECTOR_STORE_CACHE[normalized_dir].as_retriever({"k": top_k})
    raise ValueError("Retriever has not been initialized. Call initialize_retriever first.")


def retrieve_documents(query: str, top_k: int = 4, persist_dir: str | Path | None = None) -> list[dict[str, Any]]:
    retriever = get_retriever(top_k=top_k, persist_dir=persist_dir)
    return retriever.invoke(query)


def assign_citation_ids(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给每个片段打上稳定的 [Sx] 编号。

    没有这一步的话，format_retrieved_docs 每次都从 S1 重新编号：
    逐篇精读时传入的是该论文自己的片段子集，笔记里的 [S2] 与全局 citations 的 S2
    指向不同来源，引用校验会得出错误结论。
    """
    for index, doc in enumerate(docs, start=1):
        metadata = doc.setdefault("metadata", {})
        metadata["citation_id"] = f"S{index}"
    return docs


def citation_key(doc: dict[str, Any]) -> tuple[Any, ...]:
    metadata = doc.get("metadata", {})
    return (
        str(metadata.get("source", "")),
        str(metadata.get("source_name", "")),
        metadata.get("page"),
        metadata.get("chunk_index"),
    )


def register_evidence(pool: list[dict[str, Any]], docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把新证据登记进引用池并分配编号，返回带编号的 docs。

    逐篇精读时会为个别论文做定向检索，那批片段未必出现在全局 top-k 里。
    不登记的话它们没有编号，format_retrieved_docs 会从 S1 重新编号，
    笔记里的 [S1] 就与报告 Sources 里的 S1 指向不同来源。
    """
    indexed = {citation_key(item): item for item in pool}
    for doc in docs:
        key = citation_key(doc)
        existing = indexed.get(key)
        if existing is None:
            indexed[key] = doc
            pool.append(doc)
        elif existing is not doc:
            # 同一个 chunk 的另一个 dict 实例：复用池中已分配的编号
            doc.setdefault("metadata", {})["citation_id"] = existing.get("metadata", {}).get("citation_id", "")
    assign_citation_ids(pool)
    for doc in docs:
        key = citation_key(doc)
        pooled = indexed.get(key)
        if pooled is not None and pooled is not doc:
            doc["metadata"]["citation_id"] = pooled.get("metadata", {}).get("citation_id", "")
    return docs


def build_citations(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for index, doc in enumerate(assign_citation_ids(docs), start=1):
        metadata = doc.get("metadata", {})
        citations.append(
            {
                "id": metadata.get("citation_id") or f"S{index}",
                "source": metadata.get("source", "unknown"),
                "source_name": metadata.get("source_name", "unknown"),
                "paper_title": metadata.get("paper_title", ""),
                "arxiv_id": metadata.get("arxiv_id", ""),
                "page": metadata.get("page"),
                "chunk_index": metadata.get("chunk_index", 0),
                "section_title": metadata.get("section_title", ""),
                "excerpt": doc.get("page_content", "")[:200],
            }
        )
    return citations


def format_retrieved_docs(docs: list[dict[str, Any]]) -> str:
    if not docs:
        return "未检索到相关资料。"

    formatted_sections = []
    for index, doc in enumerate(docs, start=1):
        metadata = doc.get("metadata", {})
        citation_id = metadata.get("citation_id") or f"S{index}"
        source = metadata.get("source", "unknown")
        page = metadata.get("page")
        chunk_index = metadata.get("chunk_index", 0)
        section_title = metadata.get("section_title", "")
        content = doc.get("page_content", "").strip()
        locator = f"page={page}" if page is not None else "page=n/a"
        section_hint = f" | section={section_title}" if section_title else ""
        formatted_sections.append(f"[{citation_id}] source={source} | {locator} | chunk={chunk_index}{section_hint}\n{content}")
    return "\n\n".join(formatted_sections)
