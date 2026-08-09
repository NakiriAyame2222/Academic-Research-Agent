from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import maybe_save_pending_memories
from memory.long_term import init_db


def test_maybe_save_pending_memories_saves_confirmed_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        init_db(db_path)
        state = {
            "pending_memory_candidates": [
                {"memory_type": "interest", "content": "关注新方向"},
                {"memory_type": "summary", "content": "总结内容"},
            ],
            "topic": "agent memory",
        }

        saved = maybe_save_pending_memories(
            state,
            user_id="u1",
            settings={"sqlite_db_path": db_path},
            confirm_fn=lambda prompt: "y",
        )

        assert saved is True
        assert state["awaiting_memory_confirmation"] is False
