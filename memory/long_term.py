from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_PREFERENCES = {
    "language": "Chinese",
    "format": "structured",
    "tone": "academic",
    "interests": [],
}



def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """已有 data/memory.db 的兼容迁移：列不存在才 ALTER。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                preferences TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                topic TEXT,
                title TEXT,
                arxiv_id TEXT,
                problem TEXT,
                related_work TEXT,
                method TEXT,
                experiments TEXT,
                summary TEXT,
                source_url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                topic TEXT,
                memory_type TEXT,
                content TEXT,
                source_ref TEXT,
                embedding TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_artifacts (
                artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                artifact_type TEXT,
                content_json TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "memory_items", "embedding", "TEXT")
        conn.commit()



def get_user_preferences(user_id: str, db_path: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT preferences FROM user_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        return {"user_id": user_id, **DEFAULT_PREFERENCES}

    preferences = json.loads(row[0])
    return {"user_id": user_id, **DEFAULT_PREFERENCES, **preferences}



def update_user_preferences(user_id: str, preferences: dict[str, Any], db_path: str) -> None:
    serialized = json.dumps(preferences, ensure_ascii=False)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO user_preferences (user_id, preferences)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET preferences = excluded.preferences
            """,
            (user_id, serialized),
        )
        conn.commit()



def save_paper_note(note: dict[str, Any], db_path: str) -> None:
    safe_values = (
        str(note.get("session_id", "") or ""),
        str(note.get("topic", "") or ""),
        str(note.get("title", "") or ""),
        str(note.get("arxiv_id", "") or ""),
        str(note.get("problem", "") or ""),
        str(note.get("related_work", "") or ""),
        str(note.get("method", "") or ""),
        str(note.get("experiments", "") or ""),
        str(note.get("summary", "") or ""),
        str(note.get("source_url", "") or ""),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_notes (
                session_id, topic, title, arxiv_id, problem, related_work,
                method, experiments, summary, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            safe_values,
        )
        conn.commit()



def save_session_artifact(session_id: str, artifact_type: str, content: dict[str, Any], db_path: str) -> None:
    serialized = json.dumps(content, ensure_ascii=False)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM session_artifacts WHERE session_id = ? AND artifact_type = ?",
            (session_id, artifact_type),
        )
        conn.execute(
            "INSERT INTO session_artifacts (session_id, artifact_type, content_json) VALUES (?, ?, ?)",
            (session_id, artifact_type, serialized),
        )
        conn.commit()



def _row_to_memory(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "item_id": row[0],
        "topic": row[1],
        "memory_type": row[2],
        "content": row[3],
        "source_ref": row[4],
        "created_at": row[5],
        "embedding": row[6],
    }


def _fetch_all_memories(user_id: str, db_path: str) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT item_id, topic, memory_type, content, source_ref, created_at, embedding
            FROM memory_items
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_memory(row) for row in rows]


def _strip_internal_fields(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": memory.get("topic", ""),
        "memory_type": memory.get("memory_type", ""),
        "content": memory.get("content", ""),
        "source_ref": memory.get("source_ref", ""),
        "created_at": memory.get("created_at", ""),
    }


def _keyword_relevance(query: str, memory: dict[str, Any]) -> float:
    """向量不可用时的兜底：CJK 感知的词项重叠，而不是 topic 字符串精确相等。"""
    from rag.text_tokenizer import term_overlap_score

    haystack = f"{memory.get('topic', '')} {memory.get('content', '')}"
    return max(term_overlap_score(query, haystack), term_overlap_score(haystack, query) * 0.5)


def _recency_bonus(rank: int, total: int) -> float:
    if total <= 1:
        return 0.0
    return 0.1 * (1.0 - rank / total)


def backfill_memory_embeddings(user_id: str, db_path: str) -> int:
    """给历史记忆惰性补 embedding；embedder 不可用时直接返回 0。"""
    from config import get_embedder

    embedder = get_embedder()
    if embedder is None:
        return 0

    pending = [item for item in _fetch_all_memories(user_id, db_path) if not item.get("embedding")]
    if not pending:
        return 0
    try:
        vectors = embedder.embed_documents([f"{item.get('topic', '')} {item.get('content', '')}" for item in pending])
    except Exception:
        return 0
    if len(vectors) != len(pending):
        return 0

    with sqlite3.connect(db_path) as conn:
        for item, vector in zip(pending, vectors):
            conn.execute(
                "UPDATE memory_items SET embedding = ? WHERE item_id = ?",
                (json.dumps(list(vector)), item["item_id"]),
            )
        conn.commit()
    return len(pending)


def fetch_relevant_memories(user_id: str, topic: str, db_path: str, limit: int = 5) -> list[dict[str, Any]]:
    """按语义相关度召回长期记忆。

    改动前是 WHERE user_id=? AND (topic=? OR topic='')，topic 字符串精确相等 ——
    上次存的是 "llm agent memory"，这次问"agent 的记忆机制"一条都召回不到。
    现在：优先 embedding 余弦 + topic 命中加成 + 新近度；embedder 不可用时
    退化为词项重叠（仍然不是精确相等），最差情况才按时间倒序返回。
    """
    memories = _fetch_all_memories(user_id, db_path)
    if not memories:
        return []
    if not topic:
        return [_strip_internal_fields(item) for item in memories[:limit]]

    backfill_memory_embeddings(user_id, db_path)
    memories = _fetch_all_memories(user_id, db_path)

    query_vector: list[float] | None = None
    try:
        from config import get_embedder

        embedder = get_embedder()
        if embedder is not None:
            query_vector = embedder.embed_query(topic)
    except Exception:
        query_vector = None

    from rag.sparse import cosine_similarity

    normalized_topic = topic.strip().lower()
    total = len(memories)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for rank, memory in enumerate(memories):
        semantic = 0.0
        raw_embedding = memory.get("embedding")
        if query_vector is not None and raw_embedding:
            try:
                semantic = max(0.0, cosine_similarity(query_vector, json.loads(raw_embedding)))
            except (TypeError, ValueError, json.JSONDecodeError):
                semantic = 0.0

        keyword = _keyword_relevance(topic, memory)
        topic_bonus = 0.15 if str(memory.get("topic", "")).strip().lower() == normalized_topic else 0.0
        # 无 topic 的通用记忆（如输出偏好）保留一点基础分，保证仍会被带上
        general_bonus = 0.05 if not str(memory.get("topic", "")).strip() else 0.0
        score = semantic * 0.7 + keyword * 0.3 + topic_bonus + general_bonus + _recency_bonus(rank, total)
        scored.append((score, -rank, memory))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [_strip_internal_fields(memory) for _, _, memory in scored[:limit]]


def save_memory_item(user_id: str, topic: str, memory_type: str, content: str, source_ref: str, db_path: str) -> None:
    embedding_json: str | None = None
    try:
        from config import get_embedder

        embedder = get_embedder()
        if embedder is not None and content:
            embedding_json = json.dumps(list(embedder.embed_query(f"{topic} {content}")))
    except Exception:
        embedding_json = None

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO memory_items (user_id, topic, memory_type, content, source_ref, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, topic, memory_type, content, source_ref, embedding_json),
        )
        conn.commit()



def extract_preferences_from_task(task: str, report: str) -> dict[str, Any]:
    normalized_task = task.lower()
    normalized_report = report.lower()
    preferences: dict[str, Any] = {}

    if "中文" in task or "chinese" in normalized_task:
        preferences["language"] = "Chinese"
    elif "英文" in task or "english" in normalized_task:
        preferences["language"] = "English"

    if "表格" in task or "compare" in normalized_task or "比较" in task:
        preferences["format"] = "table"
    elif "markdown" in normalized_task:
        preferences["format"] = "markdown"
    elif "报告" in task or "report" in normalized_task:
        preferences["format"] = "structured"

    if "academic" in normalized_task or "学术" in task or "论文" in task:
        preferences["tone"] = "academic"
    elif "brief" in normalized_task or "简洁" in task:
        preferences["tone"] = "concise"

    interests = []
    for keyword in ["rag", "agent", "llm", "transformer", "知识管理", "arxiv", "survey"]:
        if keyword in normalized_task or keyword in normalized_report:
            interests.append(keyword)
    if interests:
        preferences["interests"] = sorted(set(interests))

    return preferences



def merge_preferences(old_prefs: dict[str, Any], new_prefs: dict[str, Any]) -> dict[str, Any]:
    merged = {**old_prefs, **new_prefs}
    merged_interests = set(old_prefs.get("interests", [])) | set(new_prefs.get("interests", []))
    merged["interests"] = sorted(merged_interests)
    return merged
