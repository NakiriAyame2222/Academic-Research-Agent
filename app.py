from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from agent.graph import build_graph
from agent.state import build_initial_state
from config import (
    apply_config_values,
    ensure_directories,
    get_settings,
    load_kv_config_file,
    persist_config_values,
)
from memory.long_term import init_db, save_memory_item, update_user_preferences
from memory.short_term import append_message, load_or_create_session, save_checkpoint
from rag.retriever import get_file_scope_dir, initialize_retriever

SUPPORTED_PAPER_EXTENSIONS = {".pdf", ".txt", ".md"}


def _print_progress_line(message: str) -> None:
    print(message, flush=True)


def _parse_index_selection(raw: str, total: int) -> list[int]:
    lowered = raw.strip().lower()
    if not lowered:
        return []
    if lowered in {"all", "a", "全部"}:
        return list(range(total))
    if lowered in {"none", "n", "不保存"}:
        return []

    selected: list[int] = []
    for part in raw.replace("，", ",").split(","):
        stripped = part.strip()
        if not stripped:
            continue
        if stripped.isdigit():
            index = int(stripped) - 1
            if 0 <= index < total:
                selected.append(index)
    return sorted(set(selected))


def maybe_save_pending_memories(
    state: dict[str, Any],
    user_id: str,
    settings: dict[str, Any],
    input_fn: Callable[[str], str] | None = None,
    confirm_fn: Callable[[str], str] | None = None,
) -> bool:
    candidates = state.get("pending_memory_candidates") or []
    state["awaiting_memory_confirmation"] = False
    state["exit_requested"] = False
    if not candidates:
        return False

    if input_fn is None:
        input_fn = input
    if confirm_fn is None:
        confirm_fn = input_fn

    print("\n退出前可保存以下长期记忆：")
    for idx, candidate in enumerate(candidates, 1):
        content = candidate.get("content") or ""
        memory_type = candidate.get("memory_type") or "summary"
        print(f"{idx}. [{memory_type}] {content}")

    selection_raw = ""
    should_prompt_for_selection = input_fn is not input and input_fn is not confirm_fn
    if should_prompt_for_selection:
        selection_raw = str(input_fn("请输入要保存的编号（逗号分隔，all 表示全部，直接回车表示不保存）: ")).strip()
        selected_indexes = _parse_index_selection(selection_raw, len(candidates))
    else:
        selected_indexes = list(range(len(candidates)))

    saved_any = False
    for index in selected_indexes:
        candidate = candidates[index]
        decision = str(confirm_fn(f"确认保存第 {index + 1} 条记忆? [y/N]: ")).strip().lower()
        if decision not in {"y", "yes"}:
            continue
        save_memory_item(
            user_id,
            state.get("topic") or state.get("initial_user_question", ""),
            candidate.get("memory_type") or "summary",
            candidate.get("content") or "",
            candidate.get("source_ref") or state.get("session_id", user_id),
            str(settings["sqlite_db_path"]),
        )
        saved_any = True

    if saved_any and state.get("user_profile"):
        update_user_preferences(user_id, state.get("user_profile", {}), str(settings["sqlite_db_path"]))

    state["pending_memory_candidates"] = []
    return saved_any


def detect_intent_mode(query: str, file_path: str | None = None) -> str:
    if file_path:
        return "paper_qa"

    possible_path = Path(query.strip())
    if possible_path.exists() and possible_path.suffix.lower() in SUPPORTED_PAPER_EXTENSIONS:
        return "paper_qa"

    lowered = query.lower()
    topic_tokens = [
        "arxiv", "survey", "review", "调研", "综述", "找论文", "research", "topic", "主题", "搜一下", "最新进展",
        "overview", "recent papers", "latest", "state of the art", "research direction", "研究方向", "研究脉络", "代表性论文",
        "发展", "收集", "我想研究", "最新方法",
    ]
    paper_tokens = [
        "论文", "这篇", "这些论文", "方法", "实验结果", "贡献", "总结", "解释", "什么", "如何", "为什么", "对比",
        "this paper", "the paper", "section", "figure", "table", "ablation", "conclusion", "future work", "methodology",
        "dataset", "authors", "latency", "result table", "算法步骤", "图", "第 ", "怎么做",
    ]

    if any(token in lowered for token in topic_tokens):
        return "topic_research"
    if any(token in lowered for token in paper_tokens):
        return "paper_qa"
    return "topic_research"


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
) -> dict[str, Any]:
    resolved_file_path = resolve_file_path(query, file_path)
    intent_mode = detect_intent_mode(query, resolved_file_path)
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


def run_chat_loop(
    user_id: str,
    session_id: str,
    file_path: str | None,
    rebuild_index: bool,
    top_k: int | None,
    resume: bool,
    initial_query: str | None = None,
    show_sources: bool = False,
) -> None:
    print("进入学术研究对话模式，输入 exit 或 quit 结束。")
    first_turn = True
    current_file = file_path
    latest_state: dict[str, Any] | None = None
    settings = get_settings()
    pending_query = (initial_query or "").strip()
    while True:
        query = pending_query or input("你: ").strip()
        pending_query = ""
        if not query:
            continue
        if query.lower() in {"exit", "quit"}:
            if latest_state:
                saved = maybe_save_pending_memories(latest_state, user_id, settings, input_fn=input, confirm_fn=input)
                print("已保存你选择的长期记忆。" if saved else "未保存长期记忆。")
                save_checkpoint(latest_state, latest_state.get("session_id", user_id))
            print("已退出。")
            break
        final_state = run_research(
            query=query,
            user_id=user_id,
            session_id=session_id,
            file_path=resolve_file_path(query, current_file),
            rebuild_index=rebuild_index if first_turn else False,
            top_k=top_k,
            resume=resume or not first_turn,
            progress_callback=_print_progress_line,
        )
        latest_state = final_state
        current_file = final_state.get("paper_path") or current_file
        print(final_state.get("final_answer") or final_state.get("final_report", ""))
        if show_sources and final_state.get("citations"):
            print("\nSources:")
            for citation in final_state["citations"]:
                locator = f"p.{citation['page']}" if citation.get("page") is not None else f"chunk {citation.get('chunk_index', 0)}"
                print(f"- {citation['id']}: {citation['source']} ({locator})")
        first_turn = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Academic Research Agent CLI")
    parser.add_argument("task", nargs="?", help="Paper question or research topic")
    parser.add_argument("--user-id", default="default", help="User identifier")
    parser.add_argument("--session-id", help="Session identifier")
    parser.add_argument("--resume", help="Resume an existing session id")
    parser.add_argument("--chat", action="store_true", help="Start interactive academic chat mode")
    parser.add_argument("--file", help="Analyze a specific paper or text file")
    parser.add_argument("--rebuild-index", action="store_true", help="Force rebuild of the retriever index")
    parser.add_argument("--top-k", type=int, help="Number of chunks or papers to retrieve")
    parser.add_argument("--show-sources", action="store_true", help="Print citations after the answer")
    parser.add_argument("--config-file", help="Load model config from a KEY=VALUE file")
    parser.add_argument("--save-config", action="store_true", help="Persist provided model settings for future runs")
    parser.add_argument("--model", help="Override LLM model for this run")
    parser.add_argument("--api-key", help="Override API key for this run")
    parser.add_argument("--base-url", help="Override base URL for this run")
    args = parser.parse_args()

    if args.config_file:
        values = load_kv_config_file(args.config_file)
        apply_config_values(values)
        if args.save_config:
            persist_config_values(values)
    if args.model:
        os.environ["RESEARCH_AGENT_LLM_MODEL"] = args.model
    if args.api_key:
        os.environ["RESEARCH_AGENT_API_KEY"] = args.api_key
    if args.base_url:
        os.environ["RESEARCH_AGENT_BASE_URL"] = args.base_url
    if args.save_config and (args.model or args.api_key or args.base_url):
        persist_config_values(
            {
                "RESEARCH_AGENT_LLM_MODEL": os.environ.get("RESEARCH_AGENT_LLM_MODEL", ""),
                "RESEARCH_AGENT_API_KEY": os.environ.get("RESEARCH_AGENT_API_KEY", ""),
                "RESEARCH_AGENT_BASE_URL": os.environ.get("RESEARCH_AGENT_BASE_URL", ""),
                "RESEARCH_AGENT_EMBEDDING_MODEL": os.environ.get("RESEARCH_AGENT_EMBEDDING_MODEL", ""),
                "RESEARCH_AGENT_EMBEDDING_API_KEY": os.environ.get("RESEARCH_AGENT_EMBEDDING_API_KEY", ""),
                "RESEARCH_AGENT_EMBEDDING_BASE_URL": os.environ.get("RESEARCH_AGENT_EMBEDDING_BASE_URL", ""),
            }
        )

    session_id = args.resume or args.session_id or args.user_id

    if args.chat:
        run_chat_loop(
            user_id=args.user_id,
            session_id=session_id,
            file_path=args.file,
            rebuild_index=args.rebuild_index,
            top_k=args.top_k,
            resume=bool(args.resume),
            initial_query=args.task,
            show_sources=args.show_sources,
        )
        return

    if args.task:
        run_chat_loop(
            user_id=args.user_id,
            session_id=session_id,
            file_path=args.file,
            rebuild_index=args.rebuild_index,
            top_k=args.top_k,
            resume=bool(args.resume),
            initial_query=args.task,
            show_sources=args.show_sources,
        )
        return

    run_chat_loop(
        user_id=args.user_id,
        session_id=session_id,
        file_path=args.file,
        rebuild_index=args.rebuild_index,
        top_k=args.top_k,
        resume=bool(args.resume),
        show_sources=args.show_sources,
    )


if __name__ == "__main__":
    main()
