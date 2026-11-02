"""SQLite schema and idempotent migrations for the storage layer."""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3
from typing import Any


SCHEMA_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial_storage",
        (
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state_json TEXT NOT NULL DEFAULT '{}',
                user_id TEXT NOT NULL DEFAULT 'default',
                topic TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS session_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(session_id, sequence)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                source_uri TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                mime_type TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                page INTEGER,
                section_title TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(document_id, chunk_index)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS document_session_links (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                linked_at TEXT NOT NULL,
                PRIMARY KEY(session_id, document_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS paper_notes (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                note_type TEXT NOT NULL DEFAULT 'note',
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                kind TEXT NOT NULL DEFAULT 'fact',
                content TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                preferences_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS research_artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, kind, artifact_key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS vector_index_outbox (
                id TEXT PRIMARY KEY,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                chunk_id TEXT REFERENCES document_chunks(id) ON DELETE CASCADE,
                memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
                entity_type TEXT NOT NULL DEFAULT 'chunk',
                operation TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                claimed_at TEXT,
                claimed_by TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS vector_index_status (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(entity_type, entity_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS migration_items (
                id TEXT PRIMARY KEY,
                migration_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_session_messages_session ON session_messages(session_id, sequence)",
            "CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id, chunk_index)",
            "CREATE INDEX IF NOT EXISTS idx_outbox_claimable ON vector_index_outbox(status, available_at, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON research_artifacts(session_id, kind, artifact_key)",
        ),
    ),
)


def apply_migrations(connection: Any, migrations: Iterable[tuple[int, str, tuple[str, ...]]] = SCHEMA_MIGRATIONS) -> None:
    """Apply all known migrations atomically and safely on repeated startup."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for version, name, statements in migrations:
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone():
            continue
        connection.execute("BEGIN")
        try:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, datetime('now'))",
                (version, name),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    # v1 databases created before the state/outbox extensions are upgraded in
    # place. Keep this compatibility repair outside schema_migrations so the
    # long-standing one-version receipt remains valid.
    for table, column, ddl in (
        ("sessions", "state_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("sessions", "user_id", "TEXT NOT NULL DEFAULT 'default'"),
        ("sessions", "topic", "TEXT NOT NULL DEFAULT ''"),
        ("vector_index_outbox", "entity_type", "TEXT NOT NULL DEFAULT 'chunk'"),
        ("vector_index_outbox", "memory_id", "TEXT REFERENCES memories(id) ON DELETE CASCADE"),
        ("documents", "local_path", "TEXT NOT NULL DEFAULT ''"),
        ("documents", "usage_type", "TEXT NOT NULL DEFAULT 'user'"),
        ("documents", "keep_local", "INTEGER NOT NULL DEFAULT 0"),
        ("documents", "deleted_at", "TEXT"),
        ("document_chunks", "content_hash", "TEXT NOT NULL DEFAULT ''"),
        ("reports", "local_path", "TEXT NOT NULL DEFAULT ''"),
        ("reports", "content_hash", "TEXT NOT NULL DEFAULT ''"),
        ("reports", "is_current", "INTEGER NOT NULL DEFAULT 0"),
        ("reports", "local_materialization_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("reports", "local_materialized_at", "TEXT"),
        ("reports", "local_materialization_error", "TEXT"),
        ("memories", "user_id", "TEXT NOT NULL DEFAULT 'default'"),
        ("memories", "topic", "TEXT NOT NULL DEFAULT ''"),
        ("memories", "source_ref", "TEXT NOT NULL DEFAULT ''"),
        ("memories", "content_hash", "TEXT NOT NULL DEFAULT ''"),
        ("paper_notes", "topic", "TEXT NOT NULL DEFAULT ''"),
        ("paper_notes", "title", "TEXT NOT NULL DEFAULT ''"),
        ("paper_notes", "arxiv_id", "TEXT NOT NULL DEFAULT ''"),
        ("paper_notes", "data_usage", "TEXT NOT NULL DEFAULT 'user'"),
    ):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            try:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError as exc:
                # A concurrent/previous repair may have added the column
                # between PRAGMA and ALTER.  Treat only that exact race as
                # success; surface all other schema errors.
                if "duplicate column name" not in str(exc).lower():
                    raise
    connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_topic ON sessions(topic, updated_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_outbox_entity ON vector_index_outbox(entity_type, chunk_id, memory_id)")
