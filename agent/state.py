from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict):
    messages: list[dict[str, str]]
    recent_messages: list[dict[str, str]]
    task: str
    current_query: str
    user_id: str
    session_id: str
    mode: str
    intent_mode: str
    session_mode: str
    topic: str
    paper_path: str
    paper_metadata: dict[str, Any]
    retrieved_docs: list[dict[str, Any]]
    research_notes: str
    user_profile: dict[str, Any]
    selected_skill: dict[str, Any]
    final_report: str
    final_answer: str
    final_synthesis: str
    citations: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    active_document: dict[str, Any]
    retrieval_scope: dict[str, Any]
    search_queries: list[str]
    search_plan: dict[str, Any]
    query_batches: list[dict[str, Any]]
    survey_candidates: list[dict[str, Any]]
    selected_surveys: list[dict[str, Any]]
    paper_candidates: list[dict[str, Any]]
    selected_papers: list[dict[str, Any]]
    paper_notes: list[dict[str, Any]]
    working_document_id: str
    working_document: dict[str, Any]
    survey_artifact: dict[str, Any]
    memory_summary: str
    conversation_summary_middle: str
    initial_user_question: str
    open_questions: list[str]
    evidence_gaps: list[str]
    long_term_memories: list[dict[str, Any]]
    pending_memory_candidates: list[dict[str, Any]]
    selected_paper_fulltext_docs: list[dict[str, Any]]
    resume_from_checkpoint: bool
    exit_requested: bool
    awaiting_memory_confirmation: bool



def build_initial_state(
    query: str,
    user_id: str = "default",
    session_id: str | None = None,
    mode: str = "report",
    file_path: str | None = None,
    intent_mode: str | None = None,
) -> ResearchState:
    current_session_id = session_id or user_id
    resolved_intent = intent_mode or ("paper_qa" if file_path else "topic_research")
    active_document = {
        "file_path": file_path,
        "source_type": "pdf" if file_path and file_path.lower().endswith(".pdf") else "text" if file_path else "",
        "index_scope": "single_file" if file_path else "corpus",
    }
    retrieval_scope = {
        "mode": "single_file" if file_path else "corpus",
        "persist_dir": "",
        "file_path": file_path or "",
    }
    initial_messages = [{"role": "user", "content": query}] if query else []
    session_mode = "paper_qa" if resolved_intent == "paper_qa" else "topic_discovery"
    return {
        "messages": initial_messages,
        "recent_messages": initial_messages,
        "task": query,
        "current_query": query,
        "user_id": user_id,
        "session_id": current_session_id,
        "mode": mode,
        "intent_mode": resolved_intent,
        "session_mode": session_mode,
        "topic": query if resolved_intent == "topic_research" else "",
        "paper_path": file_path or "",
        "paper_metadata": {},
        "retrieved_docs": [],
        "research_notes": "",
        "user_profile": {"user_id": user_id},
        "selected_skill": {},
        "final_report": "",
        "final_answer": "",
        "final_synthesis": "",
        "citations": [],
        "turns": [],
        "active_document": active_document,
        "retrieval_scope": retrieval_scope,
        "search_queries": [],
        "search_plan": {},
        "query_batches": [],
        "survey_candidates": [],
        "selected_surveys": [],
        "paper_candidates": [],
        "selected_papers": [],
        "paper_notes": [],
        "working_document_id": current_session_id,
        "working_document": {},
        "survey_artifact": {},
        "memory_summary": "",
        "conversation_summary_middle": "",
        "initial_user_question": query,
        "open_questions": [],
        "evidence_gaps": [],
        "long_term_memories": [],
        "pending_memory_candidates": [],
        "selected_paper_fulltext_docs": [],
        "resume_from_checkpoint": False,
        "exit_requested": False,
        "awaiting_memory_confirmation": False,
    }
