from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from config import get_embedder
from rag.loader import prepare_documents

_VECTOR_STORE_CACHE: dict[str, Any] = {}
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)



def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_PATTERN.findall(text)]



def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if not vector_a or not vector_b:
        return 0.0
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)



def _sparse_cosine_similarity(vector_a: dict[str, float], vector_b: dict[str, float]) -> float:
    if not vector_a or not vector_b:
        return 0.0
    common_keys = set(vector_a) & set(vector_b)
    dot = sum(vector_a[key] * vector_b[key] for key in common_keys)
    norm_a = math.sqrt(sum(value * value for value in vector_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vector_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)



def _build_sparse_index(documents: list[dict[str, Any]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    tokenized_docs = [_tokenize(doc.get("page_content", "")) for doc in documents]
    doc_freq: Counter[str] = Counter()
    for tokens in tokenized_docs:
        doc_freq.update(set(tokens))

    total_docs = max(len(documents), 1)
    idf = {term: math.log((1 + total_docs) / (1 + freq)) + 1.0 for term, freq in doc_freq.items()}

    sparse_vectors: list[dict[str, float]] = []
    for tokens in tokenized_docs:
        tf = Counter(tokens)
        length = max(len(tokens), 1)
        sparse_vectors.append({term: (count / length) * idf.get(term, 1.0) for term, count in tf.items()})
    return sparse_vectors, idf



def _build_query_sparse_vector(query: str, idf: dict[str, float]) -> dict[str, float]:
    tokens = _tokenize(query)
    tf = Counter(tokens)
    length = max(len(tokens), 1)
    return {term: (count / length) * idf.get(term, 1.0) for term, count in tf.items()}


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

        query_sparse = _build_query_sparse_vector(query, self.idf)
        scored_docs: list[tuple[float, dict[str, Any]]] = []
        for index, document in enumerate(self.documents):
            semantic_score = 0.0
            if query_embedding is not None and index < len(self.embeddings):
                semantic_score = _cosine_similarity(query_embedding, self.embeddings[index])

            sparse_score = 0.0
            if index < len(self.sparse_vectors):
                sparse_score = _sparse_cosine_similarity(query_sparse, self.sparse_vectors[index])

            combined = semantic_score * 0.7 + sparse_score * 0.3 if query_embedding is not None else sparse_score
            if combined > 0:
                scored_docs.append((combined, document))

        if not scored_docs:
            return self.documents[: self.top_k]

        scored_docs.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored_docs[: self.top_k]]


class SimpleVectorStore:
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
        return embedder.embed_documents(texts)
    except Exception:
        return []



def build_vector_store(documents: list[dict[str, Any]], persist_dir: str | None = None) -> SimpleVectorStore:
    normalized_dir = _normalize_persist_dir(persist_dir) if persist_dir else None
    embeddings = _build_embeddings(documents)
    sparse_vectors, idf = _build_sparse_index(documents)
    store = SimpleVectorStore(documents, embeddings=embeddings, sparse_vectors=sparse_vectors, idf=idf, persist_dir=normalized_dir)
    store.persist()
    if normalized_dir:
        _VECTOR_STORE_CACHE[normalized_dir] = store
    return store



def load_vector_store(persist_dir: str | Path) -> SimpleVectorStore | None:
    normalized_dir = _normalize_persist_dir(persist_dir)
    if normalized_dir in _VECTOR_STORE_CACHE:
        return _VECTOR_STORE_CACHE[normalized_dir]

    documents_file = Path(normalized_dir) / "documents.json"
    if not documents_file.exists():
        return None

    embeddings_file = Path(normalized_dir) / "embeddings.json"
    sparse_vectors_file = Path(normalized_dir) / "sparse_vectors.json"
    idf_file = Path(normalized_dir) / "idf.json"
    documents = json.loads(documents_file.read_text(encoding="utf-8"))
    embeddings = json.loads(embeddings_file.read_text(encoding="utf-8")) if embeddings_file.exists() else []
    sparse_vectors = json.loads(sparse_vectors_file.read_text(encoding="utf-8")) if sparse_vectors_file.exists() else []
    idf = json.loads(idf_file.read_text(encoding="utf-8")) if idf_file.exists() else {}
    store = SimpleVectorStore(documents, embeddings=embeddings, sparse_vectors=sparse_vectors, idf=idf, persist_dir=normalized_dir)
    _VECTOR_STORE_CACHE[normalized_dir] = store
    return store



def rebuild_vector_store(
    data_dir: str | None,
    persist_dir: str | Path,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
) -> SimpleVectorStore:
    normalized_dir = _normalize_persist_dir(persist_dir)
    clear_vector_store_cache(normalized_dir)
    documents = prepare_documents(data_dir=data_dir, chunk_size=chunk_size, chunk_overlap=chunk_overlap, file_path=file_path, file_paths=file_paths)
    return build_vector_store(documents, persist_dir=normalized_dir)



def initialize_retriever(
    data_dir: str | None,
    persist_dir: str | Path,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    rebuild: bool = False,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
) -> SimpleVectorStore:
    normalized_dir = _normalize_persist_dir(persist_dir)
    if rebuild:
        return rebuild_vector_store(data_dir=data_dir, persist_dir=normalized_dir, chunk_size=chunk_size, chunk_overlap=chunk_overlap, file_path=file_path, file_paths=file_paths)

    store = load_vector_store(normalized_dir)
    if store is not None and store.documents:
        return store

    return rebuild_vector_store(data_dir=data_dir, persist_dir=normalized_dir, chunk_size=chunk_size, chunk_overlap=chunk_overlap, file_path=file_path, file_paths=file_paths)



def get_retriever(top_k: int = 4, persist_dir: str | Path | None = None) -> SimpleRetriever:
    if persist_dir is None:
        raise ValueError("persist_dir is required")
    normalized_dir = _normalize_persist_dir(persist_dir)
    if normalized_dir in _VECTOR_STORE_CACHE:
        return _VECTOR_STORE_CACHE[normalized_dir].as_retriever({"k": top_k})
    raise ValueError("Retriever has not been initialized. Call initialize_retriever first.")



def retrieve_documents(query: str, top_k: int = 4, persist_dir: str | Path | None = None) -> list[dict[str, Any]]:
    retriever = get_retriever(top_k=top_k, persist_dir=persist_dir)
    return retriever.invoke(query)



def build_citations(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for index, doc in enumerate(docs, start=1):
        metadata = doc.get("metadata", {})
        citations.append(
            {
                "id": f"S{index}",
                "source": metadata.get("source", "unknown"),
                "source_name": metadata.get("source_name", "unknown"),
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
        source = metadata.get("source", "unknown")
        page = metadata.get("page")
        chunk_index = metadata.get("chunk_index", 0)
        section_title = metadata.get("section_title", "")
        content = doc.get("page_content", "").strip()
        locator = f"page={page}" if page is not None else "page=n/a"
        section_hint = f" | section={section_title}" if section_title else ""
        formatted_sections.append(f"[S{index}] source={source} | {locator} | chunk={chunk_index}{section_hint}\n{content}")
    return "\n\n".join(formatted_sections)
