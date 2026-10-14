from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.graph import build_graph
from agent.intent import SUPPORTED_PAPER_EXTENSIONS, classify_intent
from agent.state import build_initial_state
from agent.report_discussion import (
    build_discussion_prompt, build_research_context, classify_follow_up, is_completed_report_state,
    needs_fulltext, scope_evidence, relevant_notes,
)
from config import ensure_directories, get_settings
from memory.long_term import init_db
from memory.short_term import append_message, load_checkpoint, load_or_create_session, save_checkpoint
from rag.retriever import get_file_scope_dir, initialize_retriever


def detect_intent_mode(query: str, file_path: str | None = None) -> str:
    return classify_intent(query, file_path=file_path)


def resolve_file_path(query: str, explicit_file_path: str | None = None) -> str | None:
    if explicit_file_path:
        return explicit_file_path
    possible_path = Path(query.strip())
    if possible_path.exists() and possible_path.suffix.lower() in SUPPORTED_PAPER_EXTENSIONS:
        return str(possible_path)
    return None


def initialize_app(
    rebuild_index: bool = False,
    file_path: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    ensure_directories(settings)
    init_db(str(settings["sqlite_db_path"]))

    if file_path:
        persist_dir = get_file_scope_dir(settings["vector_store_files_dir"], file_path)
        vector_store = initialize_retriever(
            data_dir=None,
            persist_dir=persist_dir,
            chunk_size=settings["chunk_size"],
            chunk_overlap=settings["chunk_overlap"],
            rebuild=rebuild_index,
            file_path=file_path,
        )
    else:
        persist_dir = settings["vector_store_corpus_dir"]
        vector_store = initialize_retriever(
            data_dir=str(settings["papers_dir"]),
            persist_dir=persist_dir,
            chunk_size=settings["chunk_size"],
            chunk_overlap=settings["chunk_overlap"],
            rebuild=rebuild_index,
        )

    graph = build_graph()
    return {
        "settings": settings,
        "vector_store": vector_store,
        "graph": graph,
        "persist_dir": str(persist_dir),
    }


def run_research(
    query: str,
    user_id: str = "default",
    session_id: str | None = None,
    file_path: str | None = None,
    rebuild_index: bool = False,
    top_k: int | None = None,
    resume: bool = False,
    progress_callback: Callable[[str], None] | None = None,
    progress_event_callback: Callable[[dict[str, Any]], None] | None = None,
    output_path: str | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    resolved_file_path = resolve_file_path(query, file_path)
    current_session_id = session_id or user_id
    previous_state = load_checkpoint(current_session_id) if resume else None
    new_research = False
    if not resolved_file_path and is_completed_report_state(previous_state):
        if classify_follow_up(query) != "new-research":
            return run_report_discussion(
                query, previous_state, top_k=top_k,
                progress_callback=progress_callback,
                progress_event_callback=progress_event_callback,
                cancel_event=cancel_event,
            )
        # A new research request must not inherit the old report's topic or
        # working document when invoked through the graph runtime.
        resume = False
        new_research = True
    intent_mode = "topic_research" if new_research else detect_intent_mode(query, resolved_file_path)
    app_context = initialize_app(rebuild_index=rebuild_index, file_path=resolved_file_path if intent_mode == "paper_qa" else None)
    current_session_id = session_id or user_id
    mode = "paper_explain" if intent_mode == "paper_qa" else "topic_research"
    initial_state = build_initial_state(
        query=query,
        user_id=user_id,
        session_id=current_session_id,
        mode=mode,
        file_path=resolved_file_path,
        intent_mode=intent_mode,
    )

    state = load_or_create_session(current_session_id, initial_state) if resume else initial_state
    if resume and query:
        state = append_message(state, "user", query)
        state["current_query"] = query
        state["task"] = query
    state["mode"] = mode
    state["intent_mode"] = intent_mode
    state["session_mode"] = "paper_qa" if intent_mode == "paper_qa" else state.get("session_mode", "topic_discovery")
    state["session_id"] = current_session_id
    state["resume_from_checkpoint"] = resume
    state["paper_path"] = resolved_file_path or state.get("paper_path", "")
    state["progress_callback"] = progress_callback
    state["progress_event_callback"] = progress_event_callback
    state["cancel_event"] = cancel_event
    state["output_path"] = output_path or ""
    state["retrieval_scope"] = {
        "mode": "single_file" if intent_mode == "paper_qa" and resolved_file_path else "corpus",
        "persist_dir": app_context["persist_dir"],
        "file_path": resolved_file_path or state.get("active_document", {}).get("file_path", ""),
        "rebuild": rebuild_index,
        "top_k": top_k or app_context["settings"]["default_top_k"],
    }
    if resolved_file_path:
        state["active_document"] = {
            "file_path": resolved_file_path,
            "source_type": Path(resolved_file_path).suffix.lower().lstrip("."),
            "index_scope": "single_file",
        }
    elif intent_mode == "paper_qa":
        state["active_document"] = {
            "file_path": "",
            "source_type": "paper_corpus",
            "index_scope": "corpus",
        }

    final_state = app_context["graph"].invoke(state)
    save_checkpoint(final_state, current_session_id)
    return final_state


def run_report_discussion(
    query: str,
    state: dict[str, Any],
    *,
    top_k: int | None = None,
    progress_callback: Callable[[str], None] | None = None,
    progress_event_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: Any | None = None,
    invoke: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Answer from a frozen report artifact, optionally reading its local index."""
    from copy import deepcopy
    from agent.citation_check import enforce_citations
    from agent.nodes import _call_llm_with_progress
    from agent.progress import emit_progress
    from rag.retriever import build_citations, retrieve_documents, load_vector_store

    intent = classify_follow_up(query)
    if intent == "new-research":
        raise ValueError("new-research requests must use the research pipeline")
    context = deepcopy(state.get("research_context") or build_research_context(state))
    context["discussion_history"] = deepcopy(state.get("discussion_history") or [])[-6:]
    # Pronoun-only follow-ups can reuse the most recent question for retrieval.
    retrieval_query = query
    if any(word in query.casefold() for word in ("这篇", "它", "上述", "刚才", "that paper", "its ")):
        previous = context["discussion_history"][-1:] or [{}]
        retrieval_query = _discussion_query = f"{previous[0].get('query', '')} {query}".strip()
    if needs_fulltext(retrieval_query, context, intent):
        intent = "evidence-on-demand"
    state = dict(state)
    state["research_context"] = context
    state["progress_callback"] = progress_callback
    state["progress_event_callback"] = progress_event_callback
    state["cancel_event"] = cancel_event

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("报告讨论已取消")

    check_cancelled()
    docs: list[dict[str, Any]] = []
    citations = deepcopy(context.get("citations") or [])
    retrieval_error = False
    if intent == "evidence-on-demand":
        indexes = context.get("fulltext_indexes") or [context.get("shared_fulltext_index")]
        indexes = list(dict.fromkeys(str(path) for path in indexes if path))
        for path in indexes:
            check_cancelled()
            emit_progress(state, "discussion", "正在从已有全文索引核查本次问题的证据")
            limit = min(8, max(1, int(top_k or get_settings()["default_top_k"])))
            try:
                # Restore from disk after an app restart, never rebuild or download.
                store = load_vector_store(path)
                hits = retrieve_documents(retrieval_query, top_k=limit, persist_dir=path)
                scoped = scope_evidence(retrieval_query, context, hits, limit)
                # Global top-k may omit a named paper; use only its existing local
                # chunks as a sparse fallback, without re-embedding the corpus.
                if not scoped and store is not None:
                    scoped = scope_evidence(retrieval_query, context, store.documents, limit)
                docs.extend(scoped)
            except InterruptedError:
                raise
            except Exception:
                retrieval_error = True
                emit_progress(state, "discussion", "已有全文索引暂不可用；不会重新下载或搜索论文")
        docs = scope_evidence(retrieval_query, context, docs, min(8, max(1, int(top_k or 8))))
        if not docs:
            emit_progress(state, "discussion", "没有可用全文证据，基于报告与笔记说明证据边界")
        # New chunks get numbers after the report's original citation pool.
        # Do not renumber S1 in the report to mean a freshly retrieved chunk.
        used = {str(c.get("id")) for c in citations}
        for doc, citation in zip(docs, build_citations(docs)):
            original = next((c for c in citations if all(c.get(key) == citation.get(key) for key in ("source", "source_name", "page", "chunk_index"))
                             and (c.get("arxiv_id") or c.get("paper_title")) == (citation.get("arxiv_id") or citation.get("paper_title"))), None)
            if original is None:
                number = 1
                while f"S{number}" in used:
                    number += 1
                citation["id"] = f"S{number}"
                used.add(citation["id"])
                citations.append(citation)
                original = citation
            doc.setdefault("metadata", {})["citation_id"] = original["id"]
    prompt_context = {**context, "citations": citations}
    prompt = build_discussion_prompt(query, prompt_context, intent, docs)
    emit_progress(state, "discussion", "正在结合报告与相关论文笔记回答追问")
    answer = (invoke(prompt, system="你是报告讨论助手，只依据给定报告、笔记与引用证据回答。") if invoke else
              _call_llm_with_progress(state, "discussion", prompt, system="你是报告讨论助手，只依据给定报告、笔记与引用证据回答。"))
    check_cancelled()
    if not answer.strip():
        raise RuntimeError("报告讨论未生成回答")
    answer, audit = enforce_citations(answer, citations)
    state["intent_mode"] = "report_discussion"
    state["session_mode"] = "report_discussion"
    state["discussion_intent"] = intent
    state["discussion_scope"] = "当前报告 + 已选论文笔记" + (" + 按需全文证据" if docs else "")
    notes_used = bool(relevant_notes(query, list(context.get("paper_notes") or [])))
    state["discussion_sources"] = ["report"] + (["selected_paper_notes"] if notes_used else []) + (["on_demand_evidence"] if docs else [])
    state["discussion"] = {
        "scope": state["discussion_scope"],
        "sources": list(state["discussion_sources"]),
        "report_used": True,
        "selected_paper_notes_used": notes_used,
        "on_demand_evidence_used": bool(docs),
        "retrieval_requested": intent == "evidence-on-demand",
        "evidence_count": len(docs),
        "retrieval_unavailable": retrieval_error or (intent == "evidence-on-demand" and not docs),
    }
    state["current_query"] = query
    state["task"] = query
    state["final_answer"] = answer
    state["final_synthesis"] = answer
    state["citations"] = citations
    state["citation_audit"] = audit
    state["retrieved_docs"] = docs
    state["tool_trace"] = []
    state["discussion_history"] = [*state.get("discussion_history", []), {"query": query, "answer": answer, "intent": intent}]
    # Rebuild the frozen context so the next follow-up can understand recent
    # discussion references while retaining the same report and paper scope.
    context["citations"] = deepcopy(citations)
    context["discussion_history"] = deepcopy(state["discussion_history"][-6:])
    state["research_context"] = context
    state["final_report"] = context["report"]
    # Keep the actual report and its download path intact across discussions.
    append_message(state, "user", query)
    save_checkpoint(state, str(state.get("session_id") or state.get("user_id", "default")))
    return state
