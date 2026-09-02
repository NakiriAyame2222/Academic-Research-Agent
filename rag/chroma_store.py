"""Chroma 向量库后端。

RAG 首选走 Chroma（真正的向量数据库，持久化 + HNSW 近邻检索），
chromadb 未安装 / 初始化失败 / 无 embedder 时由 rag/retriever.py 降级到
原有的 SimpleVectorStore（纯 Python 手写实现）。

稠密召回交给 Chroma，稀疏 TF-IDF 仍保留在本进程内，按 0.7 / 0.3 混合 ——
与降级实现的打分语义保持一致，保证两个后端的检索质量可比。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from rag.sparse import (
    DENSE_WEIGHT,
    SPARSE_WEIGHT,
    build_query_sparse_vector,
    build_sparse_index,
    sparse_cosine_similarity,
)

try:  # pragma: no cover - 可选依赖
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:  # pragma: no cover
    chromadb = None
    ChromaSettings = None


class ChromaUnavailable(RuntimeError):
    """chromadb 不可用或无法为该目录初始化集合。"""


def chroma_available() -> bool:
    return chromadb is not None


def _collection_name(persist_dir: str) -> str:
    digest = hashlib.md5(str(persist_dir).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(persist_dir).name).strip("_") or "corpus"
    return f"{stem[:40]}_{digest}"


def _flatten_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Chroma 只接受标量 metadata，复杂值序列化为字符串。"""
    flattened: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            flattened[key] = value
        else:
            flattened[key] = json.dumps(value, ensure_ascii=False)
    return flattened


class ChromaRetriever:
    def __init__(self, store: "ChromaVectorStore", top_k: int = 4) -> None:
        self.store = store
        self.top_k = top_k

    def invoke(self, query: str) -> list[dict[str, Any]]:
        return self.store.search(query, top_k=self.top_k)


class ChromaVectorStore:
    backend = "chroma"

    def __init__(
        self,
        documents: list[dict[str, Any]],
        collection: Any,
        sparse_vectors: list[dict[str, float]] | None = None,
        idf: dict[str, float] | None = None,
        persist_dir: str | None = None,
    ) -> None:
        self.documents = documents
        self.collection = collection
        self.sparse_vectors = sparse_vectors or []
        self.idf = idf or {}
        self.persist_dir = persist_dir
        # embeddings 属性仅为与 SimpleVectorStore 接口对齐，Chroma 侧不在内存里保留向量
        self.embeddings: list[list[float]] = []

    @classmethod
    def create(
        cls,
        documents: list[dict[str, Any]],
        persist_dir: str,
        embeddings: list[list[float]],
        sparse_vectors: list[dict[str, float]] | None = None,
        idf: dict[str, float] | None = None,
    ) -> "ChromaVectorStore":
        if chromadb is None:
            raise ChromaUnavailable("chromadb is not installed")
        if not embeddings or len(embeddings) != len(documents):
            raise ChromaUnavailable("chroma backend requires one embedding per document")

        if sparse_vectors is None or idf is None:
            sparse_vectors, idf = build_sparse_index(documents)

        try:
            client = chromadb.PersistentClient(
                path=str(Path(persist_dir) / "chroma"),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            name = _collection_name(persist_dir)
            try:
                client.delete_collection(name)
            except Exception:
                pass
            collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
            collection.add(
                ids=[str(index) for index in range(len(documents))],
                embeddings=embeddings,
                documents=[doc.get("page_content", "") for doc in documents],
                metadatas=[_flatten_metadata(doc.get("metadata", {})) for doc in documents],
            )
        except ChromaUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - 依赖环境
            raise ChromaUnavailable(f"failed to initialize chroma collection: {exc}") from exc

        return cls(documents, collection, sparse_vectors=sparse_vectors, idf=idf, persist_dir=str(persist_dir))

    @classmethod
    def load(
        cls,
        documents: list[dict[str, Any]],
        persist_dir: str,
        sparse_vectors: list[dict[str, float]] | None = None,
        idf: dict[str, float] | None = None,
    ) -> "ChromaVectorStore":
        if chromadb is None:
            raise ChromaUnavailable("chromadb is not installed")
        try:
            client = chromadb.PersistentClient(
                path=str(Path(persist_dir) / "chroma"),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            collection = client.get_collection(name=_collection_name(persist_dir))
            if collection.count() != len(documents):
                raise ChromaUnavailable("chroma collection is out of sync with persisted documents")
        except ChromaUnavailable:
            raise
        except Exception as exc:
            raise ChromaUnavailable(f"failed to load chroma collection: {exc}") from exc
        return cls(documents, collection, sparse_vectors=sparse_vectors, idf=idf, persist_dir=str(persist_dir))

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> ChromaRetriever:
        search_kwargs = search_kwargs or {}
        return ChromaRetriever(self, top_k=int(search_kwargs.get("k", 4)))

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if not self.documents:
            return []

        from rag import retriever as retriever_module

        candidate_pool = max(top_k * 5, 20)
        dense_scores: dict[int, float] = {}

        embedder = retriever_module.get_embedder()
        if embedder is not None:
            try:
                query_embedding = embedder.embed_query(query)
                result = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=min(candidate_pool, len(self.documents)),
                    include=["distances"],
                )
                ids = (result.get("ids") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                for raw_id, distance in zip(ids, distances):
                    try:
                        index = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    # cosine space: distance = 1 - similarity
                    dense_scores[index] = 1.0 - float(distance)
            except Exception:
                dense_scores = {}

        query_sparse = build_query_sparse_vector(query, self.idf)
        sparse_scores: dict[int, float] = {}
        for index in range(len(self.documents)):
            if index >= len(self.sparse_vectors):
                break
            score = sparse_cosine_similarity(query_sparse, self.sparse_vectors[index])
            if score > 0:
                sparse_scores[index] = score

        if dense_scores:
            combined = {
                index: dense_scores.get(index, 0.0) * DENSE_WEIGHT + sparse_scores.get(index, 0.0) * SPARSE_WEIGHT
                for index in set(dense_scores) | set(sparse_scores)
            }
        else:
            combined = dict(sparse_scores)

        ranked = sorted((score, index) for index, score in combined.items() if score > 0)
        ranked.reverse()
        if not ranked:
            return self.documents[:top_k]
        return [self.documents[index] for _, index in ranked[:top_k]]

    def persist(self) -> None:
        """Chroma 自己持久化向量；这里只落 documents / 稀疏索引，供重载与降级共用。"""
        if not self.persist_dir:
            return
        persist_path = Path(self.persist_dir)
        persist_path.mkdir(parents=True, exist_ok=True)
        (persist_path / "documents.json").write_text(json.dumps(self.documents, ensure_ascii=False, indent=2), encoding="utf-8")
        (persist_path / "sparse_vectors.json").write_text(json.dumps(self.sparse_vectors, ensure_ascii=False), encoding="utf-8")
        (persist_path / "idf.json").write_text(json.dumps(self.idf, ensure_ascii=False), encoding="utf-8")
