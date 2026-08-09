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



def fetch_session_artifact(session_id: str, artifact_type: str, db_path: str) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_json FROM session_artifacts WHERE session_id = ? AND artifact_type = ? ORDER BY updated_at DESC LIMIT 1",
            (session_id, artifact_type),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])



def fetch_relevant_memories(user_id: str, topic: str, db_path: str, limit: int = 5) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT topic, memory_type, content, source_ref, created_at
            FROM memory_items
            WHERE user_id = ? AND (topic = ? OR topic = '')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, topic, limit),
        ).fetchall()
    return [
        {
            "topic": row[0],
            "memory_type": row[1],
            "content": row[2],
            "source_ref": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]



def save_memory_item(user_id: str, topic: str, memory_type: str, content: str, source_ref: str, db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO memory_items (user_id, topic, memory_type, content, source_ref) VALUES (?, ?, ?, ?, ?)",
            (user_id, topic, memory_type, content, source_ref),
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
