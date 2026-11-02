"""Formal Chroma adapter.

SQLite owns text and metadata.  Chroma contains only vectors, stable SQL IDs,
and scalar metadata, so a vector result is always followed by a SQL lookup.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:  # pragma: no cover
    chromadb = None
    ChromaSettings = None

from storage.database import Database
from storage.outbox import MEMORY_COLLECTION, RESEARCH_COLLECTION


class ChromaUnavailable(RuntimeError):
    pass


def chroma_available() -> bool:
    return chromadb is not None


def _flatten_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        else:
            result[str(key)] = json.dumps(value, ensure_ascii=False)
    return result


class ChromaRetriever:
    def __init__(self, store: "ChromaVectorStore", top_k: int = 4) -> None:
        self.store = store
        self.top_k = top_k

    def invoke(self, query: str) -> list[dict[str, Any]]:
        return self.store.search(query, top_k=self.top_k)


class ChromaVectorStore:
    backend = "chroma"

    def __init__(self, client: Any, db: Database | None = None, db_path: str | Path | None = None, documents: list[dict[str, Any]] | None = None, scope: str | None = None) -> None:
        self.client = client
        self.db = db
        self.db_path = str(db_path) if db_path is not None else None
        self.documents = documents or []  # compatibility view; not a persistence source
        self.scope = scope
        self.collection = client.get_or_create_collection(name=RESEARCH_COLLECTION, metadata={"hnsw:space": "cosine"})
        self.memory_collection = client.get_or_create_collection(name=MEMORY_COLLECTION, metadata={"hnsw:space": "cosine"})
        self.ready = True

    @classmethod
    def open(cls, persist_dir: str | Path, *, db: Database | None = None, db_path: str | Path | None = None, scope: str | None = None) -> "ChromaVectorStore":
        if chromadb is None:
            raise ChromaUnavailable("chromadb is not installed")
        path = Path(persist_dir)
        path.mkdir(parents=True, exist_ok=True)
        try:
            kwargs: dict[str, Any] = {"path": str(path)}
            if ChromaSettings is not None:
                kwargs["settings"] = ChromaSettings(anonymized_telemetry=False, allow_reset=True)
            client = chromadb.PersistentClient(**kwargs)
            return cls(client, db=db, db_path=db_path, scope=scope)
        except Exception as exc:
            raise ChromaUnavailable(f"failed to initialize Chroma: {exc}") from exc

    @classmethod
    def create(cls, documents: list[dict[str, Any]], persist_dir: str, embeddings: list[list[float]], sparse_vectors: Any = None, idf: Any = None, *, db: Database | None = None, db_path: str | Path | None = None) -> "ChromaVectorStore":
        if not embeddings or len(embeddings) != len(documents):
            raise ChromaUnavailable("one embedding is required per document")
        store = cls.open(persist_dir, db=db, db_path=db_path)
        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for index, document in enumerate(documents):
            metadata = dict(document.get("metadata") or {})
            ids.append(str(metadata.get("chunk_id") or metadata.get("memory_id") or f"legacy-{index}"))
            metadatas.append(_flatten_metadata(metadata))
        store.collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)
        return store

    @classmethod
    def load(cls, documents: list[dict[str, Any]] | None, persist_dir: str, sparse_vectors: Any = None, idf: Any = None, *, db: Database | None = None, db_path: str | Path | None = None) -> "ChromaVectorStore":
        return cls.open(persist_dir, db=db, db_path=db_path)

    def _collection_for(self, entity_type: str) -> Any:
        return self.memory_collection if entity_type == "memory" else self.collection

    def upsert(self, entity_type: str, entity_id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        self._collection_for(entity_type).upsert(ids=[str(entity_id)], embeddings=[embedding], metadatas=[_flatten_metadata(metadata)])

    def delete(self, entity_type: str, entity_id: str) -> None:
        self._collection_for(entity_type).delete(ids=[str(entity_id)])

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> ChromaRetriever:
        return ChromaRetriever(self, int((search_kwargs or {}).get("k", 4)))

    def _sql(self) -> Database:
        if self.db is not None:
            return self.db
        if not self.db_path:
            raise ChromaUnavailable("SQL database is required for Chroma retrieval")
        return Database(self.db_path)

    def _fetch_chunks(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        db = self._sql()
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(f"SELECT * FROM document_chunks WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        by_id = {str(row["id"]): dict(row) for row in rows}
        result: list[dict[str, Any]] = []
        for identifier in ids:
            row = by_id.get(str(identifier))
            if not row:
                continue
            metadata = json.loads(row.get("metadata_json") or "{}")
            metadata.update({"chunk_id": row["id"], "document_id": row["document_id"], "chunk_index": row["chunk_index"]})
            result.append({"page_content": row["content"], "metadata": metadata})
        return result

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        from rag import retriever as retriever_module

        embedder = retriever_module.get_embedder()
        if embedder is None or self.collection.count() == 0:
            return []
        try:
            kwargs: dict[str, Any] = {"query_embeddings": [embedder.embed_query(query)], "n_results": min(max(top_k, 1), self.collection.count()), "include": ["distances"]}
            if self.scope:
                kwargs["where"] = {"index_scope": self.scope}
            response = self.collection.query(**kwargs)
            ids = list((response.get("ids") or [[]])[0])
        except Exception:
            return []
        return self._fetch_chunks([str(identifier) for identifier in ids])[:top_k]

    def search_memories(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        from rag import retriever as retriever_module

        embedder = retriever_module.get_embedder()
        if embedder is None or self.memory_collection.count() == 0:
            return []
        response = self.memory_collection.query(query_embeddings=[embedder.embed_query(query)], n_results=min(top_k, self.memory_collection.count()), include=["distances"])
        ids = [str(value) for value in list((response.get("ids") or [[]])[0])]
        db = self._sql()
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(ids)).fetchall() if ids else []
        by_id = {str(row["id"]): dict(row) for row in rows}
        return [{"page_content": by_id[item]["content"], "metadata": {"memory_id": item, **json.loads(by_id[item].get("metadata_json") or "{}")}} for item in ids if item in by_id]

    def persist(self) -> None:
        # PersistentClient flushes its own storage; SQL/outbox are persisted separately.
        return None
