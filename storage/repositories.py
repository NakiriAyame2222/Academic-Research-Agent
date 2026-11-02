"""Repository interfaces for the SQLite storage layer."""

from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .database import Database


JSON_FIELDS = {"metadata_json", "payload_json", "preferences_json", "state_json"}


def new_id() -> str:
    return secrets.token_urlsafe(18)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RepositoryBase:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return row_dict(self.db.execute(sql, parameters).fetchone())

    def _many(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(sql, parameters).fetchall()]


class SessionRepository(RepositoryBase):
    def create(self, title: str = "", status: str = "active", metadata: Any = None, session_id: str | None = None, *, user_id: str = "default", topic: str = "", state: Any = None) -> dict[str, Any]:
        now, identifier = utc_now(), session_id or new_id()
        self.db.execute(
            "INSERT INTO sessions(id, title, status, metadata_json, state_json, user_id, topic, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (identifier, title, status, encode_json(metadata), encode_json(state), user_id, topic, now, now),
        )
        return self.get(identifier)  # type: ignore[return-value]

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def list(self, *, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql, parameters = "SELECT * FROM sessions", []
        if status is not None:
            sql += " WHERE status = ?"
            parameters.append(status)
        sql += " ORDER BY updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        return self._many(sql, tuple(parameters))

    def update(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"title", "status", "metadata_json", "state_json", "user_id", "topic"}
        updates: dict[str, Any] = {key: value for key, value in fields.items() if key in allowed}
        if "metadata" in fields:
            updates["metadata_json"] = encode_json(fields["metadata"])
        if "state" in fields:
            updates["state_json"] = encode_json(fields["state"])
        if not updates:
            return self.get(session_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.db.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", tuple(updates.values()) + (session_id,))
        return self.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self.db.transaction(immediate=True):
            cursor = self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0


class SessionMessageRepository(RepositoryBase):
    def add(self, session_id: str, role: str, content: str, *, sequence: int | None = None, metadata: Any = None, message_id: str | None = None) -> dict[str, Any]:
        if sequence is None:
            value = self.db.execute("SELECT COALESCE(MAX(sequence), -1) + 1 FROM session_messages WHERE session_id = ?", (session_id,)).fetchone()[0]
            sequence = int(value)
        identifier, now = message_id or new_id(), utc_now()
        self.db.execute(
            "INSERT INTO session_messages(id, session_id, role, content, sequence, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (identifier, session_id, role, content, sequence, encode_json(metadata), now),
        )
        return self.get(identifier)  # type: ignore[return-value]

    def get(self, message_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM session_messages WHERE id = ?", (message_id,))

    def list(self, session_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM session_messages WHERE session_id = ? ORDER BY sequence", (session_id,))

    def delete(self, message_id: str) -> bool:
        return self.db.execute("DELETE FROM session_messages WHERE id = ?", (message_id,)).rowcount > 0


class DocumentRepository(RepositoryBase):
    def upsert(self, sha256: str, *, source_uri: str = "", title: str = "", mime_type: str = "", content: str = "", metadata: Any = None, document_id: str | None = None, local_path: str = "", usage_type: str = "user", keep_local: bool = False) -> dict[str, Any]:
        now, identifier = utc_now(), document_id or new_id()
        self.db.execute(
            """INSERT INTO documents(id, sha256, source_uri, title, mime_type, content, metadata_json, created_at, updated_at, local_path, usage_type, keep_local)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO UPDATE SET source_uri=excluded.source_uri, title=excluded.title,
               mime_type=excluded.mime_type, content=excluded.content, metadata_json=excluded.metadata_json,
               local_path=excluded.local_path, usage_type=excluded.usage_type, keep_local=excluded.keep_local, updated_at=excluded.updated_at""",
            (identifier, sha256, source_uri, title, mime_type, content, encode_json(metadata), now, now, local_path, usage_type, int(keep_local)),
        )
        return self.get_by_sha256(sha256)  # type: ignore[return-value]

    create = upsert

    def get(self, document_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM documents WHERE id = ?", (document_id,))

    def get_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM documents WHERE sha256 = ?", (sha256,))

    def list(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM documents ORDER BY updated_at DESC"
        parameters: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        return self._many(sql, parameters)

    def delete(self, document_id: str) -> bool:
        return self.db.execute("DELETE FROM documents WHERE id = ?", (document_id,)).rowcount > 0


class DocumentChunkRepository(RepositoryBase):
    def upsert(self, document_id: str, chunk_index: int, content: str, *, page: int | None = None, section_title: str = "", metadata: Any = None, chunk_id: str | None = None) -> dict[str, Any]:
        now, identifier = utc_now(), chunk_id or new_id()
        self.db.execute(
            """INSERT INTO document_chunks(id, document_id, chunk_index, content, page, section_title, metadata_json, created_at, updated_at, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id, chunk_index) DO UPDATE SET content=excluded.content, page=excluded.page,
               section_title=excluded.section_title, metadata_json=excluded.metadata_json, content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
            (identifier, document_id, chunk_index, content, page, section_title, encode_json(metadata), now, now, __import__('hashlib').sha256(content.encode('utf-8')).hexdigest()),
        )
        # Indexing is asynchronous and durable; SQL remains the fact source.
        actual = self.get_by_index(document_id, chunk_index)
        actual_id = str(actual["id"]) if actual else identifier
        self.db.execute(
            "INSERT INTO vector_index_outbox(id, document_id, chunk_id, entity_type, operation, payload_json, status, attempts, available_at, created_at, updated_at) VALUES (?, ?, ?, 'chunk', 'upsert', ?, 'pending', 0, ?, ?, ?)",
            (new_id(), document_id, actual_id, encode_json({"collection": "research_chunks"}), now, now, now),
        )
        return actual  # type: ignore[return-value]

    create = upsert

    def get(self, chunk_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM document_chunks WHERE id = ?", (chunk_id,))

    def get_by_index(self, document_id: str, chunk_index: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM document_chunks WHERE document_id = ? AND chunk_index = ?", (document_id, chunk_index))

    def list(self, document_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index", (document_id,))

    def delete(self, chunk_id: str) -> bool:
        return self.db.execute("DELETE FROM document_chunks WHERE id = ?", (chunk_id,)).rowcount > 0


class DocumentSessionLinkRepository(RepositoryBase):
    def link(self, session_id: str, document_id: str) -> dict[str, Any]:
        now = utc_now()
        self.db.execute("INSERT INTO document_session_links(session_id, document_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(session_id, document_id) DO NOTHING", (session_id, document_id, now))
        return self._one("SELECT * FROM document_session_links WHERE session_id = ? AND document_id = ?", (session_id, document_id))  # type: ignore[return-value]

    create = link

    def unlink(self, session_id: str, document_id: str) -> bool:
        return self.db.execute("DELETE FROM document_session_links WHERE session_id = ? AND document_id = ?", (session_id, document_id)).rowcount > 0

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM document_session_links WHERE session_id = ? ORDER BY linked_at", (session_id,))


class ReportRepository(RepositoryBase):
    def upsert(self, session_id: str, version: int, *, status: str = "draft", content: str = "", metadata: Any = None, report_id: str | None = None) -> dict[str, Any]:
        now, identifier = utc_now(), report_id or new_id()
        self.db.execute(
            """INSERT INTO reports(id, session_id, version, status, content, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, version) DO UPDATE SET status=excluded.status, content=excluded.content,
               metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (identifier, session_id, version, status, content, encode_json(metadata), now, now),
        )
        return self.get_by_version(session_id, version)  # type: ignore[return-value]

    create = upsert

    def get_by_version(self, session_id: str, version: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM reports WHERE session_id = ? AND version = ?", (session_id, version))

    def get(self, report_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM reports WHERE id = ?", (report_id,))

    def list(self, session_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM reports WHERE session_id = ? ORDER BY version", (session_id,))

    def next_version(self, session_id: str) -> int:
        return int(self.db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE session_id = ?", (session_id,)).fetchone()[0])

    def save_next(self, session_id: str, content: str, *, status: str = "current", metadata: Any = None) -> dict[str, Any]:
        version = self.next_version(session_id)
        if status == "current":
            self.db.execute("UPDATE reports SET status='archived', is_current=0 WHERE session_id = ?", (session_id,))
        row = self.upsert(session_id, version, status=status, content=content, metadata=metadata)
        self.db.execute(
            "UPDATE reports SET is_current = ?, local_path = COALESCE(?, local_path), content_hash = ?, local_materialization_status = 'materialized', local_materialized_at = ? WHERE id = ?",
            (
                1 if status == "current" else 0,
                (metadata or {}).get("path") if isinstance(metadata, dict) else None,
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                utc_now(),
                row["id"],
            ),
        )
        return self.get(row["id"])  # type: ignore[return-value]


class PaperNoteRepository(RepositoryBase):
    def create(self, content: str, *, session_id: str | None = None, document_id: str | None = None, note_type: str = "note", metadata: Any = None, note_id: str | None = None) -> dict[str, Any]:
        identifier, now = note_id or new_id(), utc_now()
        self.db.execute("INSERT INTO paper_notes(id, session_id, document_id, note_type, content, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (identifier, session_id, document_id, note_type, content, encode_json(metadata), now, now))
        return self.get(identifier)  # type: ignore[return-value]

    def get(self, note_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM paper_notes WHERE id = ?", (note_id,))

    def list(self, *, session_id: str | None = None, document_id: str | None = None) -> list[dict[str, Any]]:
        sql, parameters = "SELECT * FROM paper_notes", []
        conditions = []
        if session_id is not None:
            conditions.append("session_id = ?")
            parameters.append(session_id)
        if document_id is not None:
            conditions.append("document_id = ?")
            parameters.append(document_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return self._many(sql + " ORDER BY updated_at DESC", tuple(parameters))

    def delete(self, note_id: str) -> bool:
        return self.db.execute("DELETE FROM paper_notes WHERE id = ?", (note_id,)).rowcount > 0


class MemoryRepository(RepositoryBase):
    def upsert(self, content: str, *, kind: str = "fact", session_id: str | None = None, importance: float = 0, metadata: Any = None, memory_id: str | None = None) -> dict[str, Any]:
        identifier, now = memory_id or new_id(), utc_now()
        self.db.execute("INSERT INTO memories(id, session_id, kind, content, importance, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (identifier, session_id, kind, content, importance, encode_json(metadata), now, now))
        self.db.execute(
            "INSERT INTO vector_index_outbox(id, memory_id, entity_type, operation, payload_json, status, attempts, available_at, created_at, updated_at) VALUES (?, ?, 'memory', 'upsert', ?, 'pending', 0, ?, ?, ?)",
            (new_id(), identifier, encode_json({"collection": "memory_items"}), now, now, now),
        )
        return self.get(identifier)  # type: ignore[return-value]

    create = upsert

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM memories WHERE id = ?", (memory_id,))

    def list(self, *, session_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        conditions, parameters = [], []
        if session_id is not None:
            conditions.append("session_id = ?")
            parameters.append(session_id)
        if kind is not None:
            conditions.append("kind = ?")
            parameters.append(kind)
        sql = "SELECT * FROM memories" + ((" WHERE " + " AND ".join(conditions)) if conditions else "")
        return self._many(sql + " ORDER BY updated_at DESC", tuple(parameters))

    def delete(self, memory_id: str) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0


class UserPreferenceRepository(RepositoryBase):
    def upsert(self, user_id: str, preferences: Any) -> dict[str, Any]:
        now = utc_now()
        self.db.execute("INSERT INTO user_preferences(user_id, preferences_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET preferences_json=excluded.preferences_json, updated_at=excluded.updated_at", (user_id, encode_json(preferences), now))
        return self.get(user_id)  # type: ignore[return-value]

    def get(self, user_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))

    def delete(self, user_id: str) -> bool:
        return self.db.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,)).rowcount > 0


class ResearchArtifactRepository(RepositoryBase):
    def upsert(self, session_id: str, kind: str, artifact_key: str, payload: Any = None, *, artifact_id: str | None = None) -> dict[str, Any]:
        now, identifier = utc_now(), artifact_id or new_id()
        self.db.execute("INSERT INTO research_artifacts(id, session_id, kind, artifact_key, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, kind, artifact_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at", (identifier, session_id, kind, artifact_key, encode_json(payload), now, now))
        return self.get_by_key(session_id, kind, artifact_key)  # type: ignore[return-value]

    create = upsert

    def get_by_key(self, session_id: str, kind: str, artifact_key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM research_artifacts WHERE session_id = ? AND kind = ? AND artifact_key = ?", (session_id, kind, artifact_key))

    def list(self, session_id: str, *, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is None:
            return self._many("SELECT * FROM research_artifacts WHERE session_id = ? ORDER BY updated_at DESC", (session_id,))
        return self._many("SELECT * FROM research_artifacts WHERE session_id = ? AND kind = ? ORDER BY updated_at DESC", (session_id, kind))


class VectorIndexOutboxRepository(RepositoryBase):
    def enqueue(self, operation: str, *, document_id: str | None = None, chunk_id: str | None = None, memory_id: str | None = None, entity_type: str = "chunk", payload: Any = None, available_at: str | None = None, outbox_id: str | None = None) -> dict[str, Any]:
        now, identifier = utc_now(), outbox_id or new_id()
        self.db.execute("INSERT INTO vector_index_outbox(id, document_id, chunk_id, memory_id, entity_type, operation, payload_json, status, attempts, available_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)", (identifier, document_id, chunk_id, memory_id, entity_type, operation, encode_json(payload), available_at or now, now, now))
        return self.get(identifier)  # type: ignore[return-value]

    create = enqueue

    def get(self, outbox_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM vector_index_outbox WHERE id = ?", (outbox_id,))

    def claim(self, *, worker_id: str, limit: int = 10, now: str | None = None) -> list[dict[str, Any]]:
        timestamp = now or utc_now()
        with self.db.transaction(immediate=True):
            rows = self.db.execute("SELECT id FROM vector_index_outbox WHERE status IN ('pending', 'retry') AND available_at <= ? ORDER BY created_at LIMIT ?", (timestamp, limit)).fetchall()
            identifiers = [row[0] for row in rows]
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                self.db.execute(f"UPDATE vector_index_outbox SET status='processing', attempts=attempts+1, claimed_at=?, claimed_by=?, updated_at=? WHERE id IN ({placeholders})", (timestamp, worker_id, timestamp, *identifiers))
        return [self.get(identifier) for identifier in identifiers]  # type: ignore[misc]

    def mark_done(self, outbox_id: str) -> dict[str, Any] | None:
        now = utc_now()
        self.db.execute("UPDATE vector_index_outbox SET status='done', updated_at=?, last_error=NULL WHERE id = ?", (now, outbox_id))
        return self.get(outbox_id)

    def retry(self, outbox_id: str, *, error: str, available_at: str | None = None) -> dict[str, Any] | None:
        now = utc_now()
        self.db.execute("UPDATE vector_index_outbox SET status='retry', available_at=?, last_error=?, claimed_at=NULL, claimed_by=NULL, updated_at=? WHERE id = ?", (available_at or now, error, now, outbox_id))
        return self.get(outbox_id)

    def dead_letter(self, outbox_id: str, *, error: str) -> dict[str, Any] | None:
        now = utc_now()
        self.db.execute("UPDATE vector_index_outbox SET status='dead_letter', last_error=?, claimed_at=NULL, claimed_by=NULL, updated_at=? WHERE id = ?", (error, now, outbox_id))
        return self.get(outbox_id)

    def pending_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) AS count FROM vector_index_outbox GROUP BY status").fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def recover_stale(self, *, stale_before: str, available_at: str | None = None) -> int:
        now = utc_now()
        cursor = self.db.execute("UPDATE vector_index_outbox SET status='retry', available_at=?, claimed_at=NULL, claimed_by=NULL, last_error=COALESCE(last_error, 'recovered stale claim'), updated_at=? WHERE status='processing' AND claimed_at < ?", (available_at or now, now, stale_before))
        return cursor.rowcount


class VectorIndexStatusRepository(RepositoryBase):
    def upsert(self, entity_type: str, entity_id: str, *, status: str, attempts: int = 0, indexed_at: str | None = None, last_error: str | None = None) -> dict[str, Any]:
        now = utc_now()
        self.db.execute("INSERT INTO vector_index_status(entity_type, entity_id, status, attempts, indexed_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(entity_type, entity_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts, indexed_at=excluded.indexed_at, last_error=excluded.last_error, updated_at=excluded.updated_at", (entity_type, entity_id, status, attempts, indexed_at, last_error, now))
        return self.get(entity_type, entity_id)  # type: ignore[return-value]

    def get(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM vector_index_status WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id))


class MigrationItemRepository(RepositoryBase):
    def upsert(self, migration_key: str, *, status: str = "pending", payload: Any = None, error: str | None = None, item_id: str | None = None) -> dict[str, Any]:
        now, identifier = utc_now(), item_id or new_id()
        self.db.execute("INSERT INTO migration_items(id, migration_key, status, payload_json, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(migration_key) DO UPDATE SET status=excluded.status, payload_json=excluded.payload_json, error=excluded.error, updated_at=excluded.updated_at", (identifier, migration_key, status, encode_json(payload), error, now, now))
        return self.get_by_key(migration_key)  # type: ignore[return-value]

    def get_by_key(self, migration_key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM migration_items WHERE migration_key = ?", (migration_key,))

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            return self._many("SELECT * FROM migration_items ORDER BY updated_at DESC")
        return self._many("SELECT * FROM migration_items WHERE status = ? ORDER BY updated_at DESC", (status,))


class Repositories:
    def __init__(self, db: Database) -> None:
        self.sessions = SessionRepository(db)
        self.session_messages = SessionMessageRepository(db)
        self.documents = DocumentRepository(db)
        self.document_chunks = DocumentChunkRepository(db)
        self.document_session_links = DocumentSessionLinkRepository(db)
        self.reports = ReportRepository(db)
        self.paper_notes = PaperNoteRepository(db)
        self.memories = MemoryRepository(db)
        self.user_preferences = UserPreferenceRepository(db)
        self.research_artifacts = ResearchArtifactRepository(db)
        self.vector_index_outbox = VectorIndexOutboxRepository(db)
        self.vector_index_status = VectorIndexStatusRepository(db)
        self.migration_items = MigrationItemRepository(db)


def repositories(db: Database) -> Repositories:
    return Repositories(db)
