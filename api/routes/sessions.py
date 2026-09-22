"""会话管理：列出 / 详情 / 删除（扫 data/checkpoints/）+ 多轮研究 + 记忆确认 + 报告下载。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import get_job_manager
from api.jobs import JobManager
from api.schemas import (
    MemorySaveRequest,
    MemorySaveResponse,
    ResearchRequest,
    SessionDetail,
    SessionSummary,
)
from api.serialize import serialize_state
from api.conversation import read_conversation
from memory.long_term import save_memory_item
from memory.short_term import CHECKPOINT_DIR, load_checkpoint

router = APIRouter(prefix="/api", tags=["sessions"])


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


@router.post("/sessions")
def create_session() -> dict:
    """创建会话，返回新的 session_id。研究任务挂在 /sessions/{sid}/research 下。"""
    from uuid import uuid4

    return {"session_id": uuid4().hex}


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions() -> list[SessionSummary]:
    """列出全部会话（扫 data/checkpoints/*.json）。"""
    if not CHECKPOINT_DIR.exists():
        return []
    summaries: list[SessionSummary] = []
    for path in sorted(CHECKPOINT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summaries.append(
            SessionSummary(
                session_id=path.stem,
                topic=str(state.get("topic") or state.get("initial_user_question") or "")[:80],
                has_report=bool(state.get("report_path")),
                updated_at=_mtime(path),
                messages_count=len(state.get("messages") or []),
            )
        )
    return summaries


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def session_detail(session_id: str) -> SessionDetail:
    """会话详情：读 checkpoint 并转成 API 结果体（含最近一次的研究结果）。"""
    state = load_checkpoint(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionDetail(
        session_id=session_id,
        updated_at="",
        result=serialize_state(state, session_id=session_id),
    )


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    """删除会话 checkpoint。sid 经路径参数进入，glob 通配符天然被文件名匹配排除。"""
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="invalid session id")
    path = CHECKPOINT_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="session not found")
    path.unlink()
    # 同步删掉 Web 层的对话日志
    try:
        from api.conversation import CONVERSATION_DIR

        (CONVERSATION_DIR / f"{session_id}.json").unlink(missing_ok=True)
    except Exception:
        pass
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/conversation")
def get_conversation(session_id: str) -> dict:
    """多轮对话历史（Web 层记账：每轮 query + 完整结果）。"""
    return {"session_id": session_id, "turns": read_conversation(session_id)}


@router.post("/sessions/{session_id}/research", status_code=202)
def submit_research(
    session_id: str,
    request: ResearchRequest,
    manager: JobManager = Depends(get_job_manager),
) -> dict:
    """提交研究任务，立刻返回 job_id（202）；run_research 在线程池里异步执行。

    多轮对话：前端对已有会话再次提交（resume=True）即可续跑，checkpoint 会带上历史。
    """
    job = manager.submit(session_id, request.model_dump())
    return {"job_id": job.id, "session_id": session_id}


# --- 长期记忆确认（CLI 的交互式确认 → REST） --------------------------------


@router.get("/sessions/{session_id}/memories")
def list_pending_memories(session_id: str) -> dict:
    """会话当前待确认的长期记忆候选。"""
    state = load_checkpoint(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    candidates = state.get("pending_memory_candidates") or []
    return {
        "candidates": [
            {
                "index": index,
                "content": item.get("content", ""),
                "memory_type": item.get("memory_type", "summary"),
                "source_ref": item.get("source_ref", ""),
            }
            for index, item in enumerate(candidates)
        ]
    }


@router.post("/sessions/{session_id}/memories", response_model=MemorySaveResponse)
def save_memories(session_id: str, request: MemorySaveRequest) -> MemorySaveResponse:
    """确认保存勾选的长期记忆（indexes 传索引列表；CLI 里逐条 y/N 确认那一步在线上默认通过）。"""
    state = load_checkpoint(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    candidates = state.get("pending_memory_candidates") or []
    settings_user = state.get("user_id", "default")

    from config import get_settings

    settings = get_settings()
    saved = 0
    for index in request.indexes:
        if not 0 <= index < len(candidates):
            continue
        candidate = candidates[index]
        save_memory_item(
            settings_user,
            state.get("topic") or state.get("initial_user_question", ""),
            candidate.get("memory_type") or "summary",
            candidate.get("content") or "",
            candidate.get("source_ref") or state.get("session_id", settings_user),
            str(settings["sqlite_db_path"]),
        )
        saved += 1

    # 已保存的候选清掉，未勾选的保留（用户可能回头再保存）
    state["pending_memory_candidates"] = [
        item for index, item in enumerate(candidates) if index not in set(request.indexes)
    ]
    from memory.short_term import save_checkpoint

    save_checkpoint(state, session_id)
    return MemorySaveResponse(saved=saved)


# --- 报告下载 --------------------------------------------------------------


@router.get("/sessions/{session_id}/report")
def download_report(session_id: str) -> FileResponse:
    """下载该会话的报告 markdown。路径来自 checkpoint 里的 report_path，经 data/ 越权校验。"""
    state = load_checkpoint(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    path = Path(str(state.get("report_path") or ""))
    if not path.is_absolute():
        raise HTTPException(status_code=404, detail="no report for this session")

    from config import get_settings

    data_dir = Path(get_settings()["data_dir"]).resolve()
    resolved = path.resolve()
    if resolved != data_dir and data_dir not in resolved.parents:
        raise HTTPException(status_code=400, detail="只允许下载 data/ 目录内的文件")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="report file missing")
    return FileResponse(resolved, media_type="text/markdown", filename=f"{session_id}_report.md")
