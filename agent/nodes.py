from __future__ import annotations

import re
from typing import Any

from agent.citation_check import enforce_citations
from agent.intent import classify_intent
from agent.json_utils import parse_json_dicts, parse_json_object, parse_string_list
from agent.llm import drain_degrade_notices, take_last_error as take_last_llm_error
from agent.llm import invoke_llm as _default_invoke_llm
from agent.llm import retry_progress
from agent.progress import emit_progress
from agent.research_artifacts import (
    add_candidate_paper_notes,
    add_fulltext_notes,
    add_reflection,
    add_report_outline,
    add_search_run,
    add_survey_sections,
    add_tool_trace,
    attach_final_survey,
    create_working_document,
    dedupe_papers,
    mark_selected_papers,
    select_top_papers,
)
from agent.tool_loop import run_tool_loop
from config import get_settings
from memory.compression import attach_memory_summary
from memory.long_term import (
    get_user_preferences,
    save_paper_note,
    save_session_artifact,
)
from memory.preferences import output_preferences_for_query
from memory.retrieval import retrieve_relevant_long_term_memories
from prompts import (
    ANSWER_SYSTEM,
    PAPER_QA_AGENT_SYSTEM,
    PLANNER_SYSTEM,
    QUERY_BATCH_SYSTEM,
    RESEARCH_SYSTEM,
    REWRITE_SYSTEM,
    SEARCH_PLAN_SYSTEM,
    SURVEY_OUTLINE_SYSTEM,
    TOPIC_AGENT_SYSTEM,
    get_answer_prompt,
    get_paper_qa_agent_goal_prompt,
    get_planner_prompt,
    get_query_batch_prompt,
    get_query_rewrite_prompt,
    get_research_prompt,
    get_search_plan_prompt,
    get_survey_outline_prompt,
    get_topic_agent_goal_prompt,
)
from rag.retriever import (
    assign_citation_ids,
    build_citations,
    format_retrieved_docs,
    register_evidence,
    retrieve_documents,
    take_backend_notices,
)
from report_templates import get_report_template
from agent import research_service
from agent.research_service import docs_for_paper as _docs_for_paper
from agent.report_discussion import persist_research_context
from tools.file import save_report
from tools.registry import ToolContext, get_toolset
from tools.search import build_selected_papers_index, search_academic_topic, search_papers

SURVEY_ARTIFACT_TYPE = "survey_artifact"
WORKING_DOCUMENT_ARTIFACT_TYPE = "working_document"

DEFAULT_OUTLINE = [
    "背景与核心问题",
    "代表方法与关键贡献",
    "实验与结果对比",
    "研究空白与未来方向",
]

MAX_FULLTEXT_PAPERS = 3


def _invoke_llm(
    prompt: str,
    system: str | None = None,
    json_object: bool = False,
    cancel_event: Any | None = None,
) -> str:
    """所有 LLM 调用的单一入口（测试通过 monkeypatch 这个名字来替身）。"""
    return _default_invoke_llm(prompt, system=system, json_object=json_object, cancel_event=cancel_event)


def _call_llm(
    prompt: str,
    system: str | None = None,
    json_object: bool = False,
    cancel_event: Any | None = None,
) -> str:
    """兼容只接受单个 prompt 参数的替身实现。"""
    try:
        return _invoke_llm(prompt, system=system, json_object=json_object, cancel_event=cancel_event)
    except TypeError:
        try:
            return _invoke_llm(prompt, system=system, json_object=json_object)
        except TypeError:
            return _invoke_llm(prompt)


def _call_llm_with_progress(
    state: dict[str, Any] | None,
    stage: str,
    prompt: str,
    system: str | None = None,
    json_object: bool = False,
) -> str:
    """带进度提示的 LLM 调用，并把能力降级提示转成进度事件。"""
    state = state or {}
    with retry_progress(lambda message: emit_progress(state, stage, message)):
        text = _call_llm(
            prompt,
            system=system,
            json_object=json_object,
            cancel_event=state.get("cancel_event"),
        )
    error = take_last_llm_error()
    if error:
        emit_progress(state, stage, error)
    for notice in drain_degrade_notices():
        emit_progress(state, stage, notice)
    return text


def _flush_backend_notices(state: dict[str, Any], stage: str = "search") -> None:
    for notice in take_backend_notices():
        emit_progress(state, stage, notice)


def _get_query(state: dict[str, Any]) -> str:
    return state.get("current_query") or state.get("task", "")


def _infer_intent_mode(state: dict[str, Any], query: str) -> str:
    """与 app.py:detect_intent_mode 共用 agent/intent.py 的同一套规则。"""
    return classify_intent(
        query,
        file_path=state.get("paper_path") or None,
        has_active_document=bool(state.get("active_document", {}).get("file_path")),
        default=state.get("intent_mode", "topic_research"),
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    """保留原函数名；实现改为带 markdown 围栏剥离的容错解析。"""
    return parse_json_object(raw)


def _rewrite_query(state: dict[str, Any], query: str) -> str:
    if not state.get("messages") or len(state.get("messages", [])) <= 1:
        return query
    emit_progress(state, "rewrite", f"正在结合上下文改写问题：{query}")
    prompt = get_query_rewrite_prompt(
        state.get("initial_user_question", ""),
        state.get("conversation_summary_middle", ""),
        state.get("recent_messages", state.get("messages", [])),
        query,
    )
    rewritten = _call_llm_with_progress(state, "rewrite", prompt, system=REWRITE_SYSTEM).strip()
    if rewritten and rewritten != query:
        emit_progress(state, "rewrite", f"问题改写完成：{rewritten}")
    return rewritten or query


def _build_topic_search_plan(state: dict[str, Any], query: str, user_profile: dict[str, Any]) -> dict[str, Any]:
    emit_progress(state, "plan", f"正在为主题生成搜索计划：{query}")
    raw = _call_llm_with_progress(
        state,
        "plan",
        get_search_plan_prompt(query, user_profile, state.get("long_term_memories", [])),
        system=SEARCH_PLAN_SYSTEM,
        json_object=True,
    )
    plan = parse_json_object(raw)
    if not plan:
        emit_progress(state, "plan", "搜索计划解析失败，使用默认计划模板")
        plan = {
            "goal": f"研究主题 {query} 的核心问题、方法、实验和未来方向",
            "subtopics": [query],
            "query_groups": ["broad", "survey", "method", "benchmark", "application"],
            "open_questions": [],
            "selection_criteria": ["代表性", "相关性", "实验充分性"],
            "report_outline": list(DEFAULT_OUTLINE),
        }
    outline = [str(item).strip() for item in plan.get("report_outline", []) if str(item).strip()]
    plan["report_outline"] = outline or list(DEFAULT_OUTLINE)
    emit_progress(
        state,
        "plan",
        f"搜索计划完成，子主题数：{len(plan.get('subtopics', [])) or 1}，报告章节：{plan['report_outline']}",
    )
    return plan


def _generate_query_batches(topic: str, search_plan: dict[str, Any], state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if state is not None:
        emit_progress(state, "plan", "正在生成分轮搜索 query batches")
    raw = _call_llm_with_progress(state, "plan", get_query_batch_prompt(topic, search_plan), system=QUERY_BATCH_SYSTEM)

    normalized: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for item in parse_json_dicts(raw):
        query_text = " ".join(str(item.get("query", "")).split())
        if not query_text:
            continue
        lowered = query_text.lower()
        if query_text == topic or lowered in {
            f"{topic.lower()} survey",
            f"{topic.lower()} review",
            f"{topic.lower()} overview",
            f"{topic.lower()} tutorial",
        }:
            continue
        if lowered in seen_queries:
            continue
        seen_queries.add(lowered)
        normalized.append({"label": str(item.get("label", query_text)), "query": query_text})
        if len(normalized) >= 12:
            break

    if normalized:
        if state is not None:
            emit_progress(state, "plan", f"query batches 已生成，共 {len(normalized)} 轮")
        return normalized

    fallback = [
        {"label": "broad", "query": topic},
        {"label": "survey", "query": f"{topic} survey"},
        {"label": "method", "query": f"{topic} methods"},
        {"label": "benchmark", "query": f"{topic} benchmark"},
        {"label": "application", "query": f"{topic} applications"},
    ]
    if state is not None:
        emit_progress(state, "plan", f"query batches 使用默认模板，共 {len(fallback)} 轮")
    return fallback


def _brief_note_from_paper(topic: str, paper: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    if state is not None:
        emit_progress(state, "notes", f"提炼候选论文摘要笔记：{paper.get('title', 'unknown')}")
    return research_service.brief_note(topic, paper, lambda prompt, **kw: _call_llm_with_progress(state, "notes", prompt, **kw))


# --- planner ---------------------------------------------------------------


def planner_agent(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = _get_query(state)
    emit_progress(state, "planner", f"开始分析任务意图：{query}")
    state["intent_mode"] = _infer_intent_mode(state, query)
    state["session_mode"] = "paper_qa" if state["intent_mode"] == "paper_qa" else state.get("session_mode", "topic_discovery")
    if state["intent_mode"] == "topic_research":
        state["topic"] = state.get("topic") or query
        emit_progress(state, "planner", f"识别为 topic research，主题：{state['topic']}")
    else:
        emit_progress(state, "planner", "识别为论文问答模式")

    user_profile = get_user_preferences(state["user_id"], str(settings["sqlite_db_path"]))
    state["long_term_memories"] = retrieve_relevant_long_term_memories(
        state["user_id"],
        state.get("topic", "") or query,
        str(settings["sqlite_db_path"]),
    )
    if state["long_term_memories"]:
        emit_progress(state, "planner", f"召回 {len(state['long_term_memories'])} 条相关长期记忆")

    template = get_report_template(state["intent_mode"])
    user_profile = {**user_profile, **output_preferences_for_query(query)}
    emit_progress(state, "planner", f"使用写作模板：{template['name']}")

    state.setdefault("initial_user_question", state.get("initial_user_question") or query)
    state["research_rounds"] = 0
    state["evidence_gaps"] = []
    state["tool_trace"] = []

    planner_prompt = get_planner_prompt(
        query,
        user_profile,
        template,
        state.get("mode", "report"),
        initial_question=state.get("initial_user_question", ""),
        middle_summary=state.get("conversation_summary_middle", ""),
        recent_messages=state.get("recent_messages", state.get("messages", [])),
    )
    emit_progress(state, "planner", "正在生成本轮任务规划摘要")
    state["task_plan"] = _call_llm_with_progress(state, "planner", planner_prompt, system=PLANNER_SYSTEM)

    if state.get("intent_mode") == "topic_research":
        search_plan = _build_topic_search_plan(state, state["topic"], user_profile)
        query_batches = _generate_query_batches(state["topic"], search_plan, state)
        state["search_plan"] = search_plan
        state["query_batches"] = query_batches
        state["search_queries"] = [item.get("query", "") for item in query_batches]
        state["open_questions"] = list(search_plan.get("open_questions", []))
        if not state.get("working_document"):
            state["working_document"] = create_working_document(state["topic"], search_plan, query_batches)
            emit_progress(state, "planner", "working document 已初始化")
        state["session_mode"] = "topic_discovery"

    state["user_profile"] = user_profile
    state["report_template"] = template
    attach_memory_summary(state)
    return state


# --- evidence agent --------------------------------------------------------


def _build_tool_context(state: dict[str, Any], rewritten_query: str) -> ToolContext:
    retrieval_scope = dict(state.get("retrieval_scope", {}))
    settings = get_settings()
    return ToolContext(
        session_id=str(state.get("session_id", state.get("user_id", "default"))),
        topic=state.get("topic", ""),
        query=rewritten_query or _get_query(state),
        top_k=int(retrieval_scope.get("top_k") or settings["default_top_k"]),
        file_path=retrieval_scope.get("file_path") or state.get("active_document", {}).get("file_path") or None,
        corpus_persist_dir=retrieval_scope.get("persist_dir"),
        fulltext_persist_dir=retrieval_scope.get("fulltext_persist_dir"),
        rebuild=bool(retrieval_scope.get("rebuild", False)),
        progress=lambda message: emit_progress(state, "tool", message),
        papers=list(state.get("paper_candidates", [])),
        cancel_event=state.get("cancel_event"),
    )


def _topic_research_deterministic(state: dict[str, Any], rewritten_query: str, top_k: int, rebuild: bool) -> dict[str, Any]:
    """降级路径：LLM 工具调用不可用时的确定性取证流程。"""
    retrieval_scope = dict(state.get("retrieval_scope", {}))
    emit_progress(state, "search", "进入 topic discovery 阶段（确定性流程）")
    search_bundle = search_academic_topic(
        state.get("topic", ""),
        max_results=max(3, top_k),
        query_batches=state.get("query_batches") or [{"label": "default", "query": rewritten_query}],
        progress_callback=lambda msg: emit_progress(state, "search", msg),
    )
    papers = dedupe_papers(search_bundle.get("papers", []))
    working_document = dict(state.get("working_document") or {})
    working_document.setdefault("topic", state.get("topic", ""))
    for run in search_bundle.get("search_runs", []):
        working_document = add_search_run(working_document, run.get("query", ""), run.get("results", []), label=run.get("label", ""))

    state["survey_candidates"] = search_bundle.get("surveys", [])
    return {
        "papers": papers,
        "working_document": working_document,
        "fulltext_persist_dir": retrieval_scope.get("fulltext_persist_dir"),
        "docs": [],
    }


def evidence_agent(state: dict[str, Any]) -> dict[str, Any]:
    """取证节点：先让 LLM 自主调用工具，不可用时回落确定性流程。"""
    settings = get_settings()
    query = _get_query(state)
    retrieval_scope = dict(state.get("retrieval_scope", {}))
    top_k = int(retrieval_scope.get("top_k") or settings["default_top_k"])
    rebuild = bool(retrieval_scope.get("rebuild", False))
    round_index = int(state.get("research_rounds", 0)) + 1
    state["research_rounds"] = round_index
    if round_index > 1:
        emit_progress(state, "search", f"进入第 {round_index} 轮取证，补齐证据缺口：{state.get('evidence_gaps', [])}")

    rewritten_query = _rewrite_query(state, query)
    state["rewritten_query"] = rewritten_query
    context = _build_tool_context(state, rewritten_query)
    specs = get_toolset(state.get("intent_mode", "topic_research"))

    if state.get("intent_mode") == "topic_research":
        goal_prompt = get_topic_agent_goal_prompt(
            state.get("topic", ""),
            state.get("search_plan", {}),
            state.get("query_batches", []),
            state.get("open_questions", []),
            state.get("evidence_gaps", []),
            round_index,
        )
        system_prompt = TOPIC_AGENT_SYSTEM
    else:
        goal_prompt = get_paper_qa_agent_goal_prompt(
            query,
            rewritten_query,
            state.get("active_document", {}),
            state.get("evidence_gaps", []),
            round_index,
        )
        system_prompt = PAPER_QA_AGENT_SYSTEM

    loop_result = run_tool_loop(state, specs, context, system_prompt, goal_prompt)
    trace = loop_result.get("trace", [])
    state["tool_trace"] = list(state.get("tool_trace", [])) + trace
    state["tool_loop_degraded"] = bool(loop_result.get("degraded"))

    if loop_result.get("cancelled"):
        state["cancelled"] = True
        return state

    if state.get("intent_mode") == "topic_research":
        state = _finalize_topic_evidence(state, context, loop_result, rewritten_query, top_k, rebuild, trace)
    else:
        state = _finalize_paper_qa_evidence(state, context, loop_result, rewritten_query, top_k, rebuild, trace)

    _flush_backend_notices(state)
    attach_memory_summary(state)
    return state


def _finalize_topic_evidence(
    state: dict[str, Any],
    context: ToolContext,
    loop_result: dict[str, Any],
    rewritten_query: str,
    top_k: int,
    rebuild: bool,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    if loop_result.get("cancelled"):
        state["cancelled"] = True
        return state

    retrieval_scope = dict(state.get("retrieval_scope", {}))
    working_document = dict(state.get("working_document") or {})
    working_document.setdefault("topic", state.get("topic", ""))

    if loop_result.get("degraded") or not context.papers:
        deterministic = _topic_research_deterministic(state, rewritten_query, top_k, rebuild)
        papers = deterministic["papers"]
        working_document = deterministic["working_document"]
        fulltext_persist_dir = deterministic["fulltext_persist_dir"]
        downloaded: list[dict[str, Any]] = []
    else:
        papers = dedupe_papers(context.papers)
        for run in context.search_runs:
            working_document = add_search_run(working_document, run.get("query", ""), run.get("results", []), label=run.get("label", ""))
        if context.surveys:
            state["survey_candidates"] = context.surveys
        fulltext_persist_dir = context.fulltext_persist_dir
        downloaded = context.downloaded

    emit_progress(state, "search", f"候选论文去重后剩余 {len(papers)} 篇")

    brief_notes = [_brief_note_from_paper(state.get("topic", ""), paper, state) for paper in papers[:8]]
    working_document = add_candidate_paper_notes(working_document, brief_notes)
    emit_progress(state, "notes", f"已生成 {len(brief_notes)} 篇候选论文简要笔记")

    selected_papers = select_top_papers(state.get("topic", ""), papers, limit=max(3, min(5, top_k)))
    # agent 已经下载过全文的论文优先作为代表论文，避免重复下载
    if downloaded:
        downloaded_ids = {str(item.get("arxiv_id", "")) for item in downloaded}
        prioritized = [paper for paper in selected_papers if str(paper.get("arxiv_id", "")) in downloaded_ids]
        rest = [paper for paper in selected_papers if str(paper.get("arxiv_id", "")) not in downloaded_ids]
        selected_papers = prioritized + rest

    working_document = mark_selected_papers(working_document, selected_papers)
    state["selected_papers"] = selected_papers
    state["paper_candidates"] = papers
    state["paper_notes"] = brief_notes
    emit_progress(state, "select", f"已选出 {len(selected_papers)} 篇代表论文")

    if selected_papers:
        indexed_ids = {str(item.get("arxiv_id", "")) for item in downloaded}
        wanted_ids = {str(paper.get("arxiv_id", "")) for paper in selected_papers[:MAX_FULLTEXT_PAPERS]}
        # agent 已索引的论文可能与最终选出的代表论文不一致（尤其是补充取证轮），差集非空就重建
        if not fulltext_persist_dir or wanted_ids - indexed_ids:
            fulltext_persist_dir, downloaded = build_selected_papers_index(
                state.get("session_id", state.get("user_id", "default")),
                selected_papers[:MAX_FULLTEXT_PAPERS],
                rebuild=True,
                progress_callback=lambda msg: emit_progress(state, "fulltext", msg),
            )

    if fulltext_persist_dir:
        retrieval_scope["fulltext_persist_dir"] = fulltext_persist_dir
        retrieval_scope["persist_dir"] = fulltext_persist_dir
        retrieval_scope["downloads"] = downloaded
        state["retrieval_scope"] = retrieval_scope
        emit_progress(state, "fulltext", f"开始基于全文索引检索关键证据：{rewritten_query or _get_query(state)}")
        fulltext_docs = retrieve_documents(
            rewritten_query or _get_query(state), top_k=max(6, top_k), persist_dir=fulltext_persist_dir
        )
        state["selected_paper_fulltext_docs"] = fulltext_docs
        state["retrieved_docs"] = fulltext_docs
        emit_progress(state, "fulltext", f"全文证据检索完成，命中 {len(fulltext_docs)} 个片段")
    else:
        state["selected_paper_fulltext_docs"] = []
        state["retrieved_docs"] = []
        emit_progress(state, "fulltext", "未选出代表论文，跳过全文阶段")

    state["citations"] = build_citations(state.get("retrieved_docs", []))
    state["working_document"] = add_tool_trace(working_document, trace)
    return state


def _finalize_paper_qa_evidence(
    state: dict[str, Any],
    context: ToolContext,
    loop_result: dict[str, Any],
    rewritten_query: str,
    top_k: int,
    rebuild: bool,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_scope = dict(state.get("retrieval_scope", {}))
    docs = context.docs
    if loop_result.get("degraded") or not docs:
        emit_progress(state, "search", f"开始检索本地论文证据：{rewritten_query or _get_query(state)}")
        docs = search_papers(
            rewritten_query or _get_query(state),
            top_k=top_k,
            file_path=retrieval_scope.get("file_path") or state.get("active_document", {}).get("file_path") or None,
            rebuild=rebuild,
            persist_dir=retrieval_scope.get("persist_dir"),
        )
    state["retrieved_docs"] = docs
    state["citations"] = build_citations(docs)
    emit_progress(state, "search", f"检索完成，命中 {len(docs)} 个片段")
    if trace:
        state["working_document"] = add_tool_trace(dict(state.get("working_document") or {}), trace)
    return state


# --- researcher ------------------------------------------------------------


def _fulltext_evidence_for_paper(
    state: dict[str, Any],
    paper: dict[str, Any],
    docs: list[dict[str, Any]],
    persist_dir: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """先在已有证据里找，找不到再针对这篇论文做一次定向检索。"""
    paper_docs, source = _docs_for_paper(paper, docs)
    if source != "fallback" or not persist_dir:
        return paper_docs, source

    targeted_query = " ".join(
        part for part in [str(paper.get("title", "")), state.get("topic", "")] if part
    ).strip()
    if not targeted_query:
        return paper_docs, source
    try:
        targeted_docs = retrieve_documents(targeted_query, top_k=8, persist_dir=persist_dir)
    except Exception:
        return paper_docs, source

    matched, matched_source = _docs_for_paper(paper, targeted_docs)
    if matched_source != "fallback":
        emit_progress(state, "research", f"定向检索到属于《{paper.get('title', 'unknown')}》的证据片段")
        return matched, f"targeted_{matched_source}"
    return paper_docs, source


def research_agent(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = _get_query(state)
    docs = state.get("retrieved_docs", [])
    docs_text = format_retrieved_docs(docs[: max(4, int(state.get("retrieval_scope", {}).get("top_k") or settings["default_top_k"]))])

    if state.get("intent_mode") == "topic_research":
        emit_progress(state, "research", "进入全文深读阶段")
        selected_papers = state.get("selected_papers", [])
        persist_dir = state.get("retrieval_scope", {}).get("fulltext_persist_dir")
        fulltext_notes: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        # 引用池：全局 top-k 打底，逐篇定向检索到的片段登记进来后统一编号
        evidence_pool: list[dict[str, Any]] = list(docs)
        assign_citation_ids(evidence_pool)

        for paper in selected_papers[:MAX_FULLTEXT_PAPERS]:
            emit_progress(state, "research", f"正在阅读代表论文：{paper.get('title', 'unknown')}")
            paper_docs, source = _fulltext_evidence_for_paper(state, paper, docs, persist_dir)
            if source == "fallback":
                emit_progress(state, "research", f"未找到属于《{paper.get('title', 'unknown')}》的专属证据，暂用通用片段兜底")
            paper_docs = register_evidence(evidence_pool, paper_docs[:3])

            note = research_service.fulltext_note(
                state.get("topic", ""), paper, paper_docs,
                lambda prompt, **kw: _call_llm_with_progress(state, "research", prompt, **kw),
            )
            note.update(evidence_status=source, evidence_count=len(paper_docs))
            fulltext_notes.append(note)
            evidence_status.append(
                {
                    "title": note["title"],
                    "arxiv_id": note["arxiv_id"],
                    "evidence_status": source,
                    "evidence_count": len(paper_docs),
                }
            )

        state["paper_notes"] = fulltext_notes
        state["evidence_status"] = evidence_status
        # 报告要引用的编号必须覆盖所有真正喂给模型的片段
        state["retrieved_docs"] = evidence_pool
        state["selected_paper_fulltext_docs"] = evidence_pool
        state["citations"] = build_citations(evidence_pool)
        state["research_notes"] = (
            "\n\n".join(
                f"【{note.get('title', 'unknown')}】\n{note.get('summary', '')}"
                for note in fulltext_notes
                if note.get("summary")
            )
            or docs_text
        )
        working_document = add_fulltext_notes(dict(state.get("working_document") or {}), fulltext_notes)
        state["working_document"] = working_document
        emit_progress(
            state,
            "research",
            f"全文笔记整理完成，共 {len(fulltext_notes)} 篇，引用池 {len(evidence_pool)} 个片段",
        )
    else:
        emit_progress(state, "research", "开始基于检索证据撰写研究笔记")
        prompt = get_research_prompt(
            query,
            docs_text,
            state.get("report_template", {}).get("guidelines", []),
            state.get("mode", "report"),
            state.get("initial_user_question", ""),
            state.get("conversation_summary_middle", ""),
            state.get("recent_messages", state.get("messages", [])),
            state.get("memory_summary", ""),
            state.get("long_term_memories", []),
            task_plan=state.get("task_plan", ""),
        )
        state["research_notes"] = _call_llm_with_progress(state, "research", prompt, system=RESEARCH_SYSTEM)
        state["evidence_status"] = []
        emit_progress(state, "research", "研究笔记生成完成")

    attach_memory_summary(state)
    return state


# --- reflect ---------------------------------------------------------------


def reflect_evidence(state: dict[str, Any]) -> dict[str, Any]:
    max_rounds = int(get_settings().get("max_research_rounds") or 2)
    round_index = int(state.get("research_rounds", 1))
    emit_progress(state, "reflect", "正在审查证据是否足够支撑结论")
    reflection = research_service.review_evidence(
        _get_query(state), state.get("intent_mode", "topic_research"), state.get("research_notes", ""),
        state.get("citations", []), state.get("evidence_status", []), state.get("open_questions", []),
        round_index, max_rounds,
        lambda prompt, **kw: _call_llm_with_progress(state, "reflect", prompt, **kw),
    )
    state["reflection"] = reflection
    state["evidence_gaps"] = reflection["evidence_gaps"]
    state["open_questions"] = reflection["open_questions"]
    state["working_document"] = add_reflection(dict(state.get("working_document") or {}), reflection)
    if reflection["sufficient"]:
        emit_progress(state, "reflect", "证据充分，进入报告撰写")
    elif reflection["stop_reason"] == "round_limit":
        emit_progress(state, "reflect", "已达研究轮数上限，报告保留未解决的证据缺口")
    else:
        emit_progress(state, "reflect", f"证据不足，准备补充取证：{reflection['evidence_gaps']}")
        if reflection["next_queries"]:
            state["query_batches"] = [{"label": f"gap_{index}", "query": query}
                                      for index, query in enumerate(reflection["next_queries"], 1)]
    attach_memory_summary(state)
    return state


def route_after_reflection(state: dict[str, Any]) -> str:
    settings = get_settings()
    max_rounds = int(settings.get("max_research_rounds") or 2)
    if state.get("evidence_gaps") and int(state.get("research_rounds", 1)) < max_rounds:
        return "evidence_agent"
    return "writer"


# --- writer ----------------------------------------------------------------


def _resolve_outline(state: dict[str, Any]) -> list[str]:
    """章节结构来自 planner 产出的 report_outline，planner 未给出时回退默认大纲。"""
    outline = [str(item).strip() for item in state.get("search_plan", {}).get("report_outline", []) if str(item).strip()]
    if outline:
        emit_progress(state, "report", f"使用 planner 生成的报告大纲：{outline}")
        return outline

    working_document = dict(state.get("working_document") or {})
    if working_document.get("paper_notes_brief"):
        emit_progress(state, "report", "planner 未给出大纲，基于工作文档现场生成")
        generated = parse_string_list(
            _call_llm_with_progress(
                state,
                "report",
                get_survey_outline_prompt(state.get("topic", ""), working_document),
                system=SURVEY_OUTLINE_SYSTEM,
            )
        )
        generated = [item for item in generated if 2 <= len(item) <= 40][:6]
        if generated:
            emit_progress(state, "report", f"已生成报告大纲：{generated}")
            return generated

    emit_progress(state, "report", "使用默认报告大纲")
    return list(DEFAULT_OUTLINE)


def _write_survey_sections(state: dict[str, Any], outline: list[str], notes: str) -> list[dict[str, str]]:
    return research_service.write_sections(
        state.get("topic", ""), outline, notes, state.get("retrieved_docs", []),
        {**dict(state.get("working_document") or {}), "user_profile": state.get("user_profile", {})},
        lambda prompt, **kw: _call_llm_with_progress(state, "report", prompt, **kw),
        lambda index, title: emit_progress(state, "report", f"正在撰写第 {index}/{len(outline)} 节：{title}"),
    )


def _compose_survey(topic: str, sections: list[dict[str, str]]) -> str:
    return research_service.compose_report(topic, sections)


def _persist_report(state: dict[str, Any], report: str) -> str:
    """把报告写到 reports_dir（或调用方指定的 output_path），返回落盘路径。"""
    explicit = str(state.get("output_path") or "").strip()
    if not report.strip():
        return ""
    # 问答由调用方提供输出路径（Web 为每个任务分配路径）；调研报告默认落盘。
    if not explicit and state.get("intent_mode") != "topic_research":
        return ""

    settings = get_settings()
    if explicit:
        target = explicit
    else:
        session_id = str(state.get("session_id", state.get("user_id", "default")))
        raw_slug = state.get("topic") or state.get("initial_user_question") or "report"
        slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", str(raw_slug)).strip("_")[:40] or "report"
        target = str(settings["reports_dir"] / f"{session_id}_{slug}.md")
    try:
        save_report(target, report)
    except OSError as exc:
        emit_progress(state, "report", f"报告写入失败：{exc}")
        return ""
    emit_progress(state, "report", f"报告已写入：{target}")
    return target


def report_writer(state: dict[str, Any]) -> dict[str, Any]:
    query = _get_query(state)
    notes = state.get("research_notes", "") or format_retrieved_docs(state.get("retrieved_docs", []))
    active_document = state.get("active_document", {})
    survey_artifact = state.get("survey_artifact") or {}
    citations = state.get("citations", [])

    if state.get("intent_mode") == "topic_research":
        emit_progress(state, "report", "开始整理综述大纲")
        outline = _resolve_outline(state)
        working_document = add_report_outline(dict(state.get("working_document") or {}), outline)
        state["working_document"] = working_document

        emit_progress(state, "report", "开始生成最终调研报告")
        sections = _write_survey_sections(state, outline, notes)
        if sections:
            final_answer = _compose_survey(state.get("topic", ""), sections)
            working_document = add_survey_sections(working_document, sections)
        else:
            emit_progress(state, "report", "逐节撰写未产出内容，回落为单次整体生成")
            final_answer = _call_llm_with_progress(
                state,
                "report",
                get_answer_prompt(
                    query,
                    notes,
                    state.get("user_profile", {}),
                    state.get("mode", "report"),
                    state.get("initial_user_question", ""),
                    state.get("conversation_summary_middle", ""),
                    state.get("recent_messages", state.get("messages", [])),
                    active_document,
                    survey_artifact={"outline": outline},
                ),
                system=ANSWER_SYSTEM,
            )

        final_answer, audit = enforce_citations(final_answer, citations)
        state["citation_audit"] = audit
        emit_progress(
            state,
            "report",
            f"引用校验：引用 {audit['total_references']} 处，非法编号 {audit['invalid_references'] or '无'}，"
            f"证据覆盖率 {audit['citation_coverage']:.0%}",
        )

        survey_artifact = {
            "topic": state.get("topic", ""),
            "outline": outline,
            "sections": sections,
            "full_text": final_answer,
            "papers": [paper.get("title", "") for paper in state.get("selected_papers", [])],
            "citations": citations,
            "citation_audit": audit,
        }
        state["survey_artifact"] = survey_artifact
        state["final_report"] = final_answer
        state["final_answer"] = final_answer
        state["final_synthesis"] = final_answer
        state["working_document"] = attach_final_survey(working_document, survey_artifact)
        emit_progress(state, "report", "调研报告生成完成")
    else:
        emit_progress(state, "report", "开始生成回答")
        final_answer = _call_llm_with_progress(
            state,
            "report",
            get_answer_prompt(
                query,
                notes,
                state.get("user_profile", {}),
                state.get("mode", "report"),
                state.get("initial_user_question", ""),
                state.get("conversation_summary_middle", ""),
                state.get("recent_messages", state.get("messages", [])),
                active_document,
                survey_artifact,
            ),
            system=ANSWER_SYSTEM,
        )
        final_answer, audit = enforce_citations(final_answer, citations)
        state["citation_audit"] = audit
        emit_progress(
            state,
            "report",
            f"引用校验：引用 {audit['total_references']} 处，非法编号 {audit['invalid_references'] or '无'}",
        )
        state["final_answer"] = final_answer
        state["final_report"] = final_answer
        state["final_synthesis"] = final_answer
        emit_progress(state, "report", "回答生成完成")

    state["report_path"] = _persist_report(state, state.get("final_answer", ""))

    # Completed reports are the durable input to the fresh-context discussion
    # flow.  This is persisted with the normal checkpoint below.
    if state.get("intent_mode") == "topic_research":
        persist_research_context(state)

    save_session_artifact(
        state.get("session_id", state.get("user_id", "default")),
        SURVEY_ARTIFACT_TYPE,
        state.get("survey_artifact", {}),
        str(get_settings()["sqlite_db_path"]),
    )
    save_session_artifact(
        state.get("session_id", state.get("user_id", "default")),
        WORKING_DOCUMENT_ARTIFACT_TYPE,
        state.get("working_document", {}),
        str(get_settings()["sqlite_db_path"]),
    )
    attach_memory_summary(state)
    return state


# --- memory ----------------------------------------------------------------


def update_memory_node(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    # 仅保存研究产物；偏好由用户设置，长期笔记由用户主动保存。

    if state.get("intent_mode") == "topic_research" and state.get("selected_papers"):
        notes_by_id = {str(note.get("arxiv_id", "")): note for note in state.get("paper_notes", [])}
        for paper in state["selected_papers"][:MAX_FULLTEXT_PAPERS]:
            note = notes_by_id.get(str(paper.get("arxiv_id", "")), {})
            save_paper_note(
                {
                    "session_id": state.get("session_id", state.get("user_id", "default")),
                    "topic": state.get("topic", ""),
                    "title": paper.get("title", ""),
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "problem": note.get("problem", "") or paper.get("summary", "")[:200],
                    "related_work": note.get("related_work", ""),
                    "method": note.get("method", ""),
                    "experiments": note.get("experiments", ""),
                    "summary": note.get("summary", "") or paper.get("summary", ""),
                    "source_url": paper.get("entry_url", ""),
                },
                str(settings["sqlite_db_path"]),
            )

    state.setdefault("pending_memory_candidates", [])  # 兼容旧会话中尚未确认的候选
    state["awaiting_memory_confirmation"] = False
    attach_memory_summary(state)
    return state
