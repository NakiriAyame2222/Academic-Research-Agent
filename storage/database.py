"""Small sqlite3 database wrapper used by the storage repositories."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .schema import apply_migrations


class Database:
    def __init__(self, path: str | Path = "app.db", *, busy_timeout_ms: int = 5000, initialize: bool = True) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.path),
            timeout=max(busy_timeout_ms, 0) / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.busy_timeout_ms = busy_timeout_ms
        self._configure()
        if initialize:
            self.migrate()
        # Lazily imported here to keep the database/schema modules independent
        # while making ``db.repositories.sessions`` convenient for callers.
        from .repositories import Repositories

        self.repositories = Repositories(self)
        self.repos = self.repositories

    def _configure(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")

    def migrate(self) -> None:
        apply_migrations(self.connection)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Commit on success and roll back on failure, including nested savepoints."""

        nested = self.connection.in_transaction
        savepoint = f"sp_{id(object())}" if nested else ""
        if nested:
            self.connection.execute(f"SAVEPOINT {savepoint}")
        else:
            self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield self.connection
        except Exception:
            if nested:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.connection.rollback()
            raise
        else:
            if nested:
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.connection.commit()

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> sqlite3.Cursor:
        return self.connection.executemany(sql, parameters)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


Storage = Database


def init_db(path: str | Path = "app.db") -> Database:
    return Database(path)
