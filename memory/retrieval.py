from __future__ import annotations

from typing import Any

from memory.long_term import fetch_relevant_memories


def retrieve_relevant_long_term_memories(
    user_id: str,
    topic: str,
    db_path: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return fetch_relevant_memories(user_id=user_id, topic=topic, db_path=db_path, limit=limit)
