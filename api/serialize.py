from __future__ import annotations

from pathlib import Path
from typing import Any

# ResearchState 里有 7 个字段只在运行时动态写入（不在 TypedDict 声明里）：
# citation_audit / tool_trace / tool_loop_degraded / evidence_status /
# research_rounds / report_path / output_path。降级或短路的运行可能根本没写它们，
# 因此一律 .get(..., 默认) 防御式读取。


def _trim_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for citation in citations or []:
        item = dict(citation)
        excerpt = item.get("excerpt")
        if isinstance(excerpt, str) and len(excerpt) > 200:
            item["excerpt"] = excerpt[:200]
        trimmed.append(item)
    return trimmed


def report_download_url_for(state: dict[str, Any], session_id: str) -> str | None:
    """report_path 是服务器绝对路径：只转成下载 URL，且仅当文件确实在 data/ 内时。"""
    path = str((state or {}).get("report_path") or "")
    if not path:
        return None
    try:
        from config import get_settings

        data_dir = Path(get_settings()["data_dir"]).resolve()
        resolved = Path(path).resolve()
    except Exception:
        return None
    if resolved != data_dir and data_dir not in resolved.parents:
        return None
    if not resolved.exists():
        return None
    return f"/api/sessions/{session_id}/report"


def serialize_state(
    state: dict[str, Any] | None,
    *,
    session_id: str,
    report_download_url: str | None = None,
) -> dict[str, Any]:
    """把 run_research 的 final_state 裁剪成安全、可 JSON 序列化的 API 响应体。

    - 只挑选需要的字段，天然剔除 callable（progress_callback / progress_event_callback）
      与大字段（selected_paper_fulltext_docs / retrieved_docs / messages 不进结果体）。
    - report_path / output_path 是服务器绝对路径，禁止外泄——只经 report_download_url
      暴露下载入口，且仅在报告文件确实存在于 data/ 内时返回。
    """
    state = state or {}
    resolved_download_url = report_download_url or report_download_url_for(state, session_id)
    return {
        "session_id": session_id or state.get("session_id", ""),
        "intent_mode": state.get("intent_mode", ""),
        # CLI 里最终答案可能落在 final_answer 或 final_report，对齐 run_chat_loop 的取值顺序
        "final_answer": state.get("final_answer") or state.get("final_report") or "",
        "survey_artifact": state.get("survey_artifact") or {},
        "citations": _trim_citations(state.get("citations") or []),
        "citation_audit": state.get("citation_audit"),  # 运行时字段，缺失即 None
        "tool_trace": state.get("tool_trace") or [],
        "tool_loop_degraded": bool(state.get("tool_loop_degraded", False)),
        "evidence_status": state.get("evidence_status") or [],
        "evidence_gaps": state.get("evidence_gaps") or [],
        "open_questions": state.get("open_questions") or [],
        "research_rounds": int(state.get("research_rounds", 0) or 0),
        "selected_papers": state.get("selected_papers") or [],
        "paper_notes": state.get("paper_notes") or [],
        "pending_memory_candidates": state.get("pending_memory_candidates") or [],
        "report_download_url": resolved_download_url,
    }
