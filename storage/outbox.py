"""Helpers for enqueueing durable Chroma index work.

SQLite is the source of truth.  These helpers only record work; the worker is
the sole component allowed to write Chroma.
"""
from __future__ import annotations

import json
from typing import Any

from .database import Database


RESEARCH_COLLECTION = "research_chunks"
MEMORY_COLLECTION = "memory_items"


def enqueue_chunk(db: Database, chunk_id: str, *, document_id: str | None = None, operation: str = "upsert", available_at: str | None = None) -> dict[str, Any]:
    return db.repositories.vector_index_outbox.enqueue(
        operation,
        document_id=document_id,
        chunk_id=chunk_id,
        entity_type="chunk",
        payload={"collection": RESEARCH_COLLECTION},
        available_at=available_at,
    )


def enqueue_memory(db: Database, memory_id: str, *, operation: str = "upsert", available_at: str | None = None) -> dict[str, Any]:
    return db.repositories.vector_index_outbox.enqueue(
        operation,
        memory_id=memory_id,
        entity_type="memory",
        payload={"collection": MEMORY_COLLECTION},
        available_at=available_at,
    )


def metadata_from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
