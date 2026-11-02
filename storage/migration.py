"""Storage migration primitives used by the one-shot migration scripts.

The migration is deliberately conservative:

* the legacy SQLite database is read only and copied to a deterministic backup;
* benchmark papers are archived, never imported into ``data/app.db``;
* test/evaluation sessions are excluded from the formal database;
* Chroma must be the actual vector backend before cleanup is allowed.

The public command line wrappers live in ``scripts/``.  Keeping the policy and
the filesystem/database operations here makes the workflow easy to exercise in
temporary fixtures without touching the project's real ``data`` directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .database import Database


RANDOM_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
TEST_SESSION_PREFIXES = (
    "test_",
    "http-",
    "verify",
    "topic-research-test",
    "capture",
    "real-pdf-rate-limit",
    "real-deep-research-eval",
)
TEST_ARTIFACT_PREFIXES = (
    "test_",
    "http-",
    "verify-pipeline",
    "verify_pipeline",
    "deep-research-live",
    "capture",
    "real-pdf-rate-limit",
    "topic-research-test",
)


@dataclass(frozen=True)
class MigrationPaths:
    root: Path
    data: Path
    legacy_db: Path
    app_db: Path
    migration_dir: Path
    manifest: Path
    backup_dir: Path
    papers: Path
    benchmark_archive: Path
    evaluation_archive: Path
    vector_store: Path
    corpus_index: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "MigrationPaths":
        project_root = Path(root).resolve()
        data = project_root / "data"
        vector_store = data / "vector_store"
        return cls(
            root=project_root,
            data=data,
            legacy_db=data / "memory.db",
            app_db=data / "app.db",
            migration_dir=data / "migration",
            manifest=data / "migration" / "migration_manifest.json",
            backup_dir=data / "migration" / "backups",
            papers=data / "papers",
            benchmark_archive=data / "archive" / "benchmark" / "papers",
            evaluation_archive=data / "archive" / "evaluation",
            vector_store=vector_store,
            corpus_index=vector_store / "corpus",
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_random_session_id(value: Any) -> bool:
    return bool(RANDOM_SESSION_RE.fullmatch(str(value or "").strip().lower()))


def is_test_session_id(value: Any) -> bool:
    session_id = str(value or "").strip().lower()
    return is_random_session_id(session_id) or session_id.startswith(TEST_SESSION_PREFIXES)


def _stable_id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _manifest(paths: MigrationPaths) -> dict[str, Any]:
    if not paths.manifest.exists():
        return {}
    try:
        loaded = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _same_file(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and _sha256(left) == _sha256(right)


def _move_path(source: Path, destination: Path) -> str:
    """Move one path without overwriting an unrelated existing archive item."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return "already_moved"
    if destination.exists():
        if source.is_file() and _same_file(source, destination):
            source.unlink()
            return "deduplicated"
        raise RuntimeError(f"archive destination already exists with different content: {destination}")
    shutil.move(str(source), str(destination))
    return "moved"


def _move_papers(paths: MigrationPaths) -> list[str]:
    moved: list[str] = []
    if not paths.papers.exists():
        return moved
    paths.benchmark_archive.mkdir(parents=True, exist_ok=True)
    for child in sorted(paths.papers.iterdir()):
        result = _move_path(child, paths.benchmark_archive / child.name)
        if result in {"moved", "deduplicated"}:
            moved.append(str(child.relative_to(paths.root)))
    if paths.papers.exists() and not any(paths.papers.iterdir()):
        paths.papers.rmdir()
    return moved


def _move_evaluation_artifacts(paths: MigrationPaths) -> list[str]:
    moved: list[str] = []
    paths.evaluation_archive.mkdir(parents=True, exist_ok=True)
    candidates: list[Path] = []
    for item in paths.data.rglob("*"):
        if not item.name.startswith("real-deep-research-eval"):
            continue
        relative_parts = set(item.relative_to(paths.data).parts)
        if "archive" in relative_parts or "migration" in relative_parts:
            continue
        candidates.append(item)
    # Move parents first; descendants then disappear with a moved directory.
    for item in sorted(candidates, key=lambda value: (len(value.parts), str(value))):
        if not item.exists():
            continue
        result = _move_path(item, paths.evaluation_archive / item.name)
        if result in {"moved", "deduplicated"}:
            moved.append(str(item.relative_to(paths.root)))
    return moved


def _backup_legacy_db(paths: MigrationPaths) -> str | None:
    if not paths.legacy_db.exists():
        return None
    digest = _sha256(paths.legacy_db)
    backup = paths.backup_dir / f"memory.db.{digest[:16]}.bak"
    paths.backup_dir.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(paths.legacy_db, backup)
    elif not _same_file(paths.legacy_db, backup):
        raise RuntimeError(f"legacy database backup collision: {backup}")
    return str(backup.relative_to(paths.root))


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _legacy_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _ensure_session(connection: sqlite3.Connection, session_id: str, now: str) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO sessions(id, title, status, metadata_json, created_at, updated_at)
           VALUES (?, '', 'archived', '{}', ?, ?)""",
        (session_id, now, now),
    )


def _migrate_legacy_database(paths: MigrationPaths) -> dict[str, int]:
    counts = {"user_preferences": 0, "paper_notes": 0, "memories": 0, "research_artifacts": 0, "skipped_test_rows": 0, "deleted_random_sessions": 0}
    if not paths.legacy_db.exists():
        # A clean installation still needs the formal schema before the
        # manifest is emitted.
        with Database(paths.app_db):
            pass
        return counts

    with sqlite3.connect(paths.legacy_db) as legacy, Database(paths.app_db) as target:
        tables = _legacy_tables(legacy)
        now = utc_now()
        with target.transaction(immediate=True):
            random_sessions = [row[0] for row in target.execute("SELECT id FROM sessions").fetchall() if is_random_session_id(row[0])]
            for session_id in random_sessions:
                target.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            counts["deleted_random_sessions"] = len(random_sessions)
            if "user_preferences" in tables:
                for row in legacy.execute("SELECT user_id, preferences FROM user_preferences").fetchall():
                    user_id, preferences = str(row[0] or "default"), row[1] or "{}"
                    try:
                        parsed = json.loads(preferences) if isinstance(preferences, str) else preferences
                    except json.JSONDecodeError:
                        parsed = {"legacy_raw": str(preferences)}
                    target.execute(
                        """INSERT INTO user_preferences(user_id, preferences_json, updated_at) VALUES (?, ?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET preferences_json=excluded.preferences_json,
                           updated_at=excluded.updated_at""",
                        (user_id, json.dumps(parsed or {}, ensure_ascii=False, separators=(",", ":")), now),
                    )
                    counts["user_preferences"] += 1

            if "paper_notes" in tables:
                columns = _table_columns(legacy, "paper_notes")
                selected = [
                    "note_id", "session_id", "topic", "title", "arxiv_id", "problem",
                    "related_work", "method", "experiments", "summary", "source_url",
                    "created_at", "updated_at",
                ]
                selected = [column for column in selected if column in columns]
                for raw in legacy.execute(f"SELECT {', '.join(selected)} FROM paper_notes").fetchall():
                    item = dict(zip(selected, raw))
                    session_id = str(item.get("session_id") or "").strip()
                    if is_test_session_id(session_id):
                        counts["skipped_test_rows"] += 1
                        continue
                    note_key = str(item.get("note_id") or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest())
                    note_id = _stable_id("legacy-note", note_key)
                    effective_session = session_id or _stable_id("legacy-session", note_key)
                    _ensure_session(target.connection, effective_session, str(item.get("created_at") or now))
                    content = str(item.get("summary") or "").strip()
                    if not content:
                        content = "\n".join(
                            f"{label}: {item.get(field) or ''}"
                            for label, field in (("Problem", "problem"), ("Related work", "related_work"), ("Method", "method"), ("Experiments", "experiments"))
                            if item.get(field)
                        ) or str(item.get("title") or "legacy paper note")
                    metadata = {key: item.get(key) for key in ("topic", "title", "arxiv_id", "source_url") if item.get(key) is not None}
                    target.execute(
                        """INSERT OR IGNORE INTO paper_notes(id, session_id, document_id, note_type, content,
                           metadata_json, created_at, updated_at) VALUES (?, ?, NULL, 'legacy-paper-note', ?, ?, ?, ?)""",
                        (note_id, effective_session, content, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                         str(item.get("created_at") or now), str(item.get("updated_at") or now)),
                    )
                    target.repositories.migration_items.upsert(
                        f"legacy:paper_notes:{note_key}", status="migrated", payload={"id": note_id}, item_id=_stable_id("migration", f"paper_notes:{note_key}"),
                    )
                    counts["paper_notes"] += 1

            if "memory_items" in tables:
                columns = _table_columns(legacy, "memory_items")
                selected = ["item_id", "user_id", "topic", "memory_type", "content", "source_ref", "created_at"]
                selected = [column for column in selected if column in columns]
                for raw in legacy.execute(f"SELECT {', '.join(selected)} FROM memory_items").fetchall():
                    item = dict(zip(selected, raw))
                    source_ref = str(item.get("source_ref") or "").strip()
                    if is_test_session_id(source_ref):
                        counts["skipped_test_rows"] += 1
                        continue
                    key = str(item.get("item_id") or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest())
                    memory_id = _stable_id("legacy-memory", key)
                    target.execute(
                        """INSERT OR IGNORE INTO memories(id, session_id, kind, content, importance, metadata_json,
                           created_at, updated_at) VALUES (?, NULL, ?, ?, 0, ?, ?, ?)""",
                        (memory_id, str(item.get("memory_type") or "legacy"), str(item.get("content") or ""),
                         json.dumps({"user_id": item.get("user_id"), "topic": item.get("topic"), "source_ref": source_ref}, ensure_ascii=False, separators=(",", ":")),
                         str(item.get("created_at") or now), str(item.get("created_at") or now)),
                    )
                    target.repositories.migration_items.upsert(
                        f"legacy:memory_items:{key}", status="migrated", payload={"id": memory_id}, item_id=_stable_id("migration", f"memory_items:{key}"),
                    )
                    counts["memories"] += 1

            if "session_artifacts" in tables:
                columns = _table_columns(legacy, "session_artifacts")
                selected = ["artifact_id", "session_id", "artifact_type", "content_json", "updated_at"]
                selected = [column for column in selected if column in columns]
                for raw in legacy.execute(f"SELECT {', '.join(selected)} FROM session_artifacts").fetchall():
                    item = dict(zip(selected, raw))
                    session_id = str(item.get("session_id") or "").strip()
                    if not session_id or is_test_session_id(session_id):
                        counts["skipped_test_rows"] += 1
                        continue
                    _ensure_session(target.connection, session_id, str(item.get("updated_at") or now))
                    key = str(item.get("artifact_id") or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest())
                    target.execute(
                        """INSERT INTO research_artifacts(id, session_id, kind, artifact_key, payload_json, created_at, updated_at)
                           VALUES (?, ?, ?, 'legacy', ?, ?, ?)
                           ON CONFLICT(session_id, kind, artifact_key) DO UPDATE SET payload_json=excluded.payload_json,
                           updated_at=excluded.updated_at""",
                        (_stable_id("legacy-artifact", key), session_id, str(item.get("artifact_type") or "legacy"),
                         str(item.get("content_json") or "{}"), str(item.get("updated_at") or now), str(item.get("updated_at") or now)),
                    )
                    counts["research_artifacts"] += 1
    return counts


def _build_chroma(paths: MigrationPaths, builder: Callable[..., Any] | None = None) -> dict[str, Any]:
    # The benchmark archive is explicitly excluded from the formal database.
    # Build only from SQL facts that were actually migrated; an empty formal
    # corpus is a valid, ready Chroma installation.
    try:
        if builder is None:
            from rag.chroma_store import ChromaVectorStore
            from storage import Database

            with Database(paths.app_db) as db:
                store = ChromaVectorStore.open(paths.data / "chroma", db=db)
                documents = int(db.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0])
                if documents:
                    from storage.worker import ChromaOutboxWorker

                    ChromaOutboxWorker(db, paths.data / "chroma").drain()
                backend = str(getattr(store, "backend", ""))
        else:
            store = builder(data_dir=None, persist_dir=str(paths.data / "chroma"), use_embeddings=True)
            backend = str(getattr(store, "backend", ""))
            documents = len(getattr(store, "documents", []) or [])
        if backend != "chroma":
            return {"status": "incomplete", "reason": f"builder returned backend {backend or 'unknown'}, not chroma", "documents": documents}
        return {"status": "complete", "backend": backend, "documents": documents, "source": "app.db"}
    except Exception as exc:
        return {"status": "incomplete", "reason": f"Chroma build failed: {type(exc).__name__}: {exc}"}


def run_migration(
    root: str | Path,
    *,
    execute: bool,
    chroma_builder: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the migration.  ``execute`` is mandatory to make accidental runs impossible."""
    if not execute:
        raise ValueError("migration requires --execute; dry-run is intentionally unsupported")
    paths = MigrationPaths.from_root(root)
    paths.data.mkdir(parents=True, exist_ok=True)
    backup = _backup_legacy_db(paths)
    db_counts = _migrate_legacy_database(paths)
    moved_papers = _move_papers(paths)
    moved_evaluation = _move_evaluation_artifacts(paths)
    chroma = _build_chroma(paths, chroma_builder)
    result: dict[str, Any] = {
        "manifest_version": 1,
        "migration_status": "completed",
        "created_at": utc_now(),
        "root": str(paths.root),
        "formal_database": str(paths.app_db.relative_to(paths.root)),
        "legacy_database": str(paths.legacy_db.relative_to(paths.root)),
        "legacy_database_backup": backup,
        "migrated_rows": db_counts,
        "moved_benchmark_papers": moved_papers,
        "moved_evaluation_artifacts": moved_evaluation,
        "chroma": chroma,
        "cleanup_allowed": chroma.get("status") == "complete",
        "cleanup_completed": False,
    }
    _write_json_atomic(paths.manifest, result)
    return result


def _artifact_is_test(path: Path) -> bool:
    name = path.name.lower()
    stem = path.stem.lower()
    if name.startswith(TEST_ARTIFACT_PREFIXES) or stem.startswith(TEST_ARTIFACT_PREFIXES):
        return True
    first = re.split(r"[_-]", stem, maxsplit=1)[0]
    return is_random_session_id(first)


def _iter_cleanup_candidates(paths: MigrationPaths) -> list[Path]:
    candidates: list[Path] = []
    for root in (paths.data / "reports", paths.data / "checkpoints", paths.data / "conversations"):
        if root.exists():
            candidates.extend(item for item in root.iterdir() if _artifact_is_test(item))
    if paths.data.exists():
        for item in paths.data.iterdir():
            if item.name in {"archive", "migration", "vector_store", "app.db", "memory.db"}:
                continue
            if _artifact_is_test(item):
                candidates.append(item)
    # Topic scheduler scopes are addressed by the md5 of the session id.  They
    # are not part of the formal corpus; remove only scopes derived from the
    # random session ids recorded in the legacy database.
    if paths.vector_store.joinpath("sessions").exists() and paths.legacy_db.exists():
        with sqlite3.connect(paths.legacy_db) as legacy:
            tables = _legacy_tables(legacy)
            if "paper_notes" in tables:
                sessions = {str(row[0] or "") for row in legacy.execute("SELECT session_id FROM paper_notes")}
                sessions |= {str(row[0] or "") for row in legacy.execute("SELECT session_id FROM session_artifacts")} if "session_artifacts" in tables else set()
                hashes = {hashlib.md5(session.encode("utf-8")).hexdigest()[:12] for session in sessions if is_random_session_id(session)}
                candidates.extend(item for item in paths.vector_store.joinpath("sessions").iterdir() if item.name in hashes)
    return candidates


def verify_migration(root: str | Path, *, require_clean: bool = False) -> tuple[bool, list[str]]:
    paths = MigrationPaths.from_root(root)
    errors: list[str] = []
    manifest = _manifest(paths)
    if not manifest:
        errors.append("migration manifest is missing or invalid")
    if not paths.app_db.exists():
        errors.append("formal database data/app.db is missing")
    else:
        try:
            # Verification is read-only: do not initialize or migrate the
            # database merely because someone asked for a receipt check.
            with sqlite3.connect(paths.app_db) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                required = {"schema_migrations", "sessions", "paper_notes", "memories", "research_artifacts"}
                missing = sorted(required - tables)
                if missing:
                    errors.append(f"formal database missing tables: {', '.join(missing)}")
                random_count = sum(1 for row in db.execute("SELECT id FROM sessions").fetchall() if is_random_session_id(row[0])) if "sessions" in tables else 0
                if random_count:
                    errors.append(f"formal database contains {random_count} random-hash sessions")
        except Exception as exc:
            errors.append(f"formal database cannot be opened: {type(exc).__name__}: {exc}")
    if paths.legacy_db.exists() and not manifest.get("legacy_database_backup"):
        errors.append("legacy memory.db exists without a recorded backup")
    if paths.papers.exists() and any(paths.papers.iterdir()):
        errors.append("legacy data/papers still contains files")
    for location in (paths.data / "reports", paths.data / "checkpoints", paths.data / "conversations"):
        if location.exists() and any(item.name.startswith("real-deep-research-eval") for item in location.iterdir()):
            errors.append(f"evaluation artifacts remain outside archive: {location.relative_to(paths.root)}")
    chroma = manifest.get("chroma", {}) if isinstance(manifest, dict) else {}
    if chroma.get("status") != "complete" or chroma.get("backend") != "chroma":
        errors.append(f"Chroma build incomplete: {chroma.get('reason', 'missing Chroma receipt')}")
    if require_clean:
        leftovers = _iter_cleanup_candidates(paths)
        if paths.legacy_db.exists():
            leftovers.append(paths.legacy_db)
        if leftovers:
            errors.append(f"cleanup leftovers remain: {len(leftovers)}")
    return not errors, errors


def cleanup_legacy_data(root: str | Path, *, execute: bool) -> dict[str, Any]:
    if not execute:
        raise ValueError("cleanup requires --execute; dry-run is intentionally unsupported")
    paths = MigrationPaths.from_root(root)
    valid, errors = verify_migration(paths.root)
    if not valid:
        raise RuntimeError("validation failed; cleanup is forbidden: " + "; ".join(errors))
    deleted: list[str] = []
    for candidate in _iter_cleanup_candidates(paths):
        if candidate.is_dir():
            shutil.rmtree(candidate)
        elif candidate.exists():
            candidate.unlink()
        deleted.append(str(candidate.relative_to(paths.root)))
    if paths.legacy_db.exists():
        paths.legacy_db.unlink()
        deleted.append(str(paths.legacy_db.relative_to(paths.root)))
    manifest = _manifest(paths)
    manifest.update({"cleanup_completed": True, "cleanup_completed_at": utc_now(), "deleted_legacy_artifacts": deleted})
    _write_json_atomic(paths.manifest, manifest)
    clean, clean_errors = verify_migration(paths.root, require_clean=True)
    if not clean:
        raise RuntimeError("cleanup verification failed: " + "; ".join(clean_errors))
    return manifest
