"""Standard-library SQLite storage foundation."""

from .database import Database, Storage, init_db
from .repositories import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentSessionLinkRepository,
    MemoryRepository,
    MigrationItemRepository,
    PaperNoteRepository,
    Repositories,
    ReportRepository,
    ResearchArtifactRepository,
    SessionMessageRepository,
    SessionRepository,
    UserPreferenceRepository,
    VectorIndexOutboxRepository,
    VectorIndexStatusRepository,
    new_id,
    repositories,
    utc_now,
)
from .schema import SCHEMA_MIGRATIONS, apply_migrations
from .outbox import MEMORY_COLLECTION, RESEARCH_COLLECTION, enqueue_chunk, enqueue_memory
from .worker import ChromaOutboxWorker
from .migration import (
    MigrationPaths,
    cleanup_legacy_data,
    is_random_session_id,
    is_test_session_id,
    run_migration,
    verify_migration,
)

__all__ = [
    "Database",
    "Storage",
    "init_db",
    "Repositories",
    "repositories",
    "apply_migrations",
    "SCHEMA_MIGRATIONS",
    "new_id",
    "utc_now",
    "SessionRepository",
    "SessionMessageRepository",
    "DocumentRepository",
    "DocumentChunkRepository",
    "DocumentSessionLinkRepository",
    "ReportRepository",
    "PaperNoteRepository",
    "MemoryRepository",
    "UserPreferenceRepository",
    "ResearchArtifactRepository",
    "VectorIndexOutboxRepository",
    "VectorIndexStatusRepository",
    "MigrationItemRepository",
    "MigrationPaths",
    "run_migration",
    "verify_migration",
    "cleanup_legacy_data",
    "is_random_session_id",
    "is_test_session_id",
    "ChromaOutboxWorker",
    "RESEARCH_COLLECTION",
    "MEMORY_COLLECTION",
    "enqueue_chunk",
    "enqueue_memory",
]
