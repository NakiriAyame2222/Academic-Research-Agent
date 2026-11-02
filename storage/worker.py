"""Single-writer SQL outbox worker for Chroma."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from config import get_embedder
from .database import Database
from .outbox import MEMORY_COLLECTION, RESEARCH_COLLECTION, metadata_from_json
from .repositories import utc_now


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ChromaOutboxWorker:
    """Process at most one claim at a time and never write Chroma elsewhere."""

    def __init__(self, db: Database, chroma_dir: str | Path, *, embedder_factory: Callable[[], Any] = get_embedder, poll_interval: float = 0.5, stale_after: float = 300.0, max_attempts: int = 10, worker_id: str | None = None) -> None:
        self.db = db
        self.chroma_dir = Path(chroma_dir)
        self.embedder_factory = embedder_factory
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.max_attempts = max_attempts
        self.worker_id = worker_id or f"chroma-worker-{uuid.uuid4().hex[:10]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self.last_error: str | None = None

    def _store(self):
        from rag.chroma_store import ChromaVectorStore

        return ChromaVectorStore.open(str(self.chroma_dir), db=self.db)

    def recover_stale(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.stale_after)
        return self.db.repositories.vector_index_outbox.recover_stale(stale_before=cutoff.isoformat())

    def _mark_failure(self, item: dict[str, Any], exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"[:500]
        self.last_error = message
        attempts = int(item.get("attempts") or 0)
        if attempts >= self.max_attempts:
            self.db.repositories.vector_index_outbox.dead_letter(item["id"], error=message)
            return
        delay = min(3600, 2 ** max(0, attempts - 1))
        available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        self.db.repositories.vector_index_outbox.retry(item["id"], error=message, available_at=available)

    def process_once(self) -> bool:
        """Process one item.  Missing embedding leaves pending rows untouched."""
        self.recover_stale()
        try:
            embedder = self.embedder_factory()
        except Exception as exc:
            # A provider outage is not an index attempt: keep SQL work pending
            # until the provider becomes available again.
            self.last_error = f"{type(exc).__name__}: {exc}"[:500]
            return False
        if embedder is None:
            return False
        items = self.db.repositories.vector_index_outbox.claim(worker_id=self.worker_id, limit=1)
        if not items:
            return False
        item = items[0]
        try:
            with self._write_lock:
                store = self._store()
                entity_type = str(item.get("entity_type") or "chunk")
                if item.get("operation") == "delete":
                    store.delete(entity_type, str(item.get("chunk_id") or item.get("memory_id")))
                else:
                    if entity_type == "memory":
                        row = self.db.repositories.memories.get(str(item["memory_id"]))
                        if not row:
                            raise KeyError(f"memory not found: {item['memory_id']}")
                        metadata = metadata_from_json(row.get("metadata_json"))
                        text = " ".join(filter(None, [row.get("topic", ""), row.get("kind", ""), row["content"]]))
                        metadata.update({"memory_id": row["id"], "kind": row.get("kind", "fact")})
                        entity_id = row["id"]
                    else:
                        row = self.db.repositories.document_chunks.get(str(item["chunk_id"]))
                        if not row:
                            raise KeyError(f"chunk not found: {item['chunk_id']}")
                        metadata = metadata_from_json(row.get("metadata_json"))
                        text = " ".join(filter(None, [metadata.get("paper_title", ""), row.get("section_title", ""), row["content"]]))
                        metadata.update({"chunk_id": row["id"], "document_id": row["document_id"], "chunk_index": row["chunk_index"]})
                        entity_id = row["id"]
                    vector = embedder.embed_query(text)
                    store.upsert(entity_type, entity_id, vector, metadata)
            self.db.repositories.vector_index_outbox.mark_done(item["id"])
            self.db.repositories.vector_index_status.upsert(
                entity_type,
                str(item.get("chunk_id") or item.get("memory_id")),
                status="ready",
                attempts=int(item.get("attempts") or 0),
                indexed_at=utc_now(),
            )
            return True
        except Exception as exc:
            self._mark_failure(item, exc)
            self.db.repositories.vector_index_status.upsert(
                str(item.get("entity_type") or "chunk"),
                str(item.get("chunk_id") or item.get("memory_id")),
                status="failed" if int(item.get("attempts") or 0) >= self.max_attempts else "retry",
                attempts=int(item.get("attempts") or 0),
                last_error=str(exc)[:500],
            )
            return True

    def drain(self, *, limit: int | None = None) -> int:
        processed = 0
        while limit is None or processed < limit:
            if not self.process_once():
                break
            processed += 1
        return processed

    def _run(self) -> None:
        self.recover_stale()
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
            self._stop.wait(self.poll_interval)

    def start(self) -> "ChromaOutboxWorker":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.worker_id, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def health(self) -> dict[str, Any]:
        counts = self.db.repositories.vector_index_outbox.pending_counts()
        try:
            from rag.chroma_store import chroma_available

            ready = chroma_available() and self._store().ready
        except Exception as exc:  # Chroma is intentionally optional at startup.
            ready = False
            self.last_error = f"{type(exc).__name__}: {exc}"[:500]
        return {"ready": ready, "outbox": counts, "last_error": self.last_error}
