from __future__ import annotations

import re
from typing import Any

from agent.citation_check import enforce_citations
from agent.intent import classify_intent
from agent.json_utils import parse_json_dicts, parse_json_object, parse_string_list
from agent.llm import drain_degrade_notices, take_last_error as take_last_llm_error
from agent.llm import invoke_llm as _default_invoke_llm
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
    merge_preferences,
    save_paper_note,
    save_session_artifact,
)
from memory.preferences import infer_preferences
from memory.retrieval import retrieve_relevant_long_term_memories
from prompts import (
    ANSWER_SYSTEM,
    MEMORY_SYSTEM,
    NOTE_SYSTEM,
    PAPER_QA_AGENT_SYSTEM,
    PLANNER_SYSTEM,
    QUERY_BATCH_SYSTEM,
    REFLECT_SYSTEM,
    RESEARCH_SYSTEM,
    REWRITE_SYSTEM,
    SEARCH_PLAN_SYSTEM,
    SURVEY_OUTLINE_SYSTEM,
    SURVEY_SECTION_SYSTEM,
    TOPIC_AGENT_SYSTEM,
    get_answer_prompt,
    get_fulltext_note_prompt,
    get_memory_selection_prompt,
    get_paper_note_prompt,
    get_paper_qa_agent_goal_prompt,
    get_planner_prompt,
    get_query_batch_prompt,
    get_query_rewrite_prompt,
    get_reflection_prompt,
    get_research_prompt,
    get_search_plan_prompt,
    get_survey_outline_prompt,
    get_survey_section_prompt,
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
from skills.loader import (
    get_skill_output_preferences,
    get_skill_steps,
    load_all_skills,
    select_skill,
)
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
_ARXIV_ID_CLEAN_PATTERN = re.compile(r"[^0-9a-zA-Z]+")


def _invoke_llm(prompt: str, system: str | None = None, json_object: bool = False) -> str:
    """所有 LLM 调用的单一入口（测试通过 monkeypatch 这个名字来替身）。"""
    return _default_invoke_llm(prompt, system=system, json_object=json_object)


def _call_llm(prompt: str, system: str | None = None, json_object: bool = False) -> str:
    """兼容只接受单个 prompt 参数的替身实现。"""
    try:
        return _invoke_llm(prompt, system=system, json_object=json_object)
    except TypeError:
        return _invoke_llm(prompt)


def _call_llm_with_progress(
    state: dict[str, Any],
    stage: str,
    prompt: str,
    system: str | None = None,
    json_object: bool = False,
) -> str:
    """带进度提示的 LLM 调用：网络类失败会被吞掉，这里把原因报出来。"""
    text = _call_llm(prompt, system=system, json_object=json_object)
    error = take_last_llm_error()
    if error:
        emit_progress(state, stage, f"LLM 调用失败，本步走降级路径：{error}")
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
    prompt = get_paper_note_prompt(topic, paper)
    raw = _call_llm_with_progress(state, "notes", prompt, system=NOTE_SYSTEM, json_object=True)
    parsed = parse_json_object(raw)
    return {
        "title": paper.get("title", ""),
        "arxiv_id": str(paper.get("arxiv_id", "") or ""),
        "published": paper.get("published", ""),
        "authors": paper.get("authors", []),
        "entry_url": paper.get("entry_url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "query": paper.get("query", ""),
        "query_label": paper.get("query_label", ""),
        "problem": parsed.get("problem", ""),
        "related_work": parsed.get("related_work", ""),
        "method": parsed.get("method", ""),
        "experiments": parsed.get("experiments", ""),
        "summary": parsed.get("summary", paper.get("summary", "")),
        "selection_status": "candidate",
    }


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

    skills = load_all_skills(str(settings["skills_dir"]))
    selected_skill = select_skill(query, skills)
    if selected_skill:
        emit_progress(state, "planner", f"命中技能：{selected_skill.get('name', 'unknown')}")
    else:
        emit_progress(state, "planner", "未命中任何技能，按通用流程处理")

    # skill 的 output_style 作为默认输出风格；用户已保存的偏好优先级更高
    skill_preferences = get_skill_output_preferences(selected_skill)
    if skill_preferences:
        user_profile = {**skill_preferences, **user_profile}

    state.setdefault("initial_user_question", state.get("initial_user_question") or query)
    state["research_rounds"] = 0
    state["evidence_gaps"] = []
    state["tool_trace"] = []

    planner_prompt = get_planner_prompt(
        query,
        user_profile,
        selected_skill,
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
    state["selected_skill"] = selected_skill
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


def _normalize_arxiv_id(value: str) -> str:
    return _ARXIV_ID_CLEAN_PATTERN.sub("", str(value or "")).lower()


def _docs_for_paper(paper: dict[str, Any], docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """把证据块归属到具体论文：优先按 arxiv_id 匹配 chunk metadata，逐级回退。"""
    arxiv_id = str(paper.get("arxiv_id", "") or "")
    normalized_id = _normalize_arxiv_id(arxiv_id)

    if normalized_id:
        matched = [doc for doc in docs if _normalize_arxiv_id(doc.get("metadata", {}).get("arxiv_id", "")) == normalized_id]
        if matched:
            return matched, "arxiv_id"

        matched = [
            doc
            for doc in docs
            if normalized_id and normalized_id in _normalize_arxiv_id(doc.get("metadata", {}).get("source_name", ""))
        ]
        if matched:
            return matched, "source_name_prefix"

    title = str(paper.get("title", "") or "").strip()
    if title:
        matched = [
            doc
            for doc in docs
            if title in str(doc.get("metadata", {}).get("paper_title", ""))
            or title in str(doc.get("metadata", {}).get("source_name", ""))
        ]
        if matched:
            return matched, "title"

    return docs[:2], "fallback"


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


def _format_fulltext_note(parsed: dict[str, Any], raw: str) -> str:
    """把结构化笔记字段拼成可读文本（而不是直接拼 JSON 源码）。"""
    field_labels = [
        ("problem", "研究问题"),
        ("method", "方法"),
        ("experiments", "实验与结果"),
        ("strengths", "优势"),
        ("limitations", "局限"),
        ("summary", "总体结论"),
    ]
    lines = [f"{label}：{str(parsed[key]).strip()}" for key, label in field_labels if str(parsed.get(key, "")).strip()]
    return "\n".join(lines) if lines else raw.strip()


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

            evidence_text = format_retrieved_docs(paper_docs)
            prompt = get_fulltext_note_prompt(state.get("topic", ""), paper, evidence_text)
            raw = _call_llm_with_progress(state, "research", prompt, system=NOTE_SYSTEM, json_object=True)
            parsed = parse_json_object(raw)

            note = {
                "title": paper.get("title", ""),
                "arxiv_id": str(paper.get("arxiv_id", "") or ""),
                "problem": parsed.get("problem", ""),
                "method": parsed.get("method", ""),
                "experiments": parsed.get("experiments", ""),
                "strengths": parsed.get("strengths", ""),
                "limitations": parsed.get("limitations", ""),
                "summary": _format_fulltext_note(parsed, raw),
                "evidence_status": source,
                "evidence_count": len(paper_docs),
                "citation_ids": [item.get("metadata", {}).get("citation_id", "") for item in paper_docs],
                "selection_status": "selected",
            }
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
            get_skill_steps(state.get("selected_skill", {})) if isinstance(state.get("selected_skill"), dict) else [],
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
    """审查证据充分性，决定回边补证还是进入撰写。evidence_gaps 非空且未达轮次上限时回 evidence_agent。"""
    settings = get_settings()
    max_rounds = int(settings.get("max_research_rounds") or 2)
    round_index = int(state.get("research_rounds", 1))

    if round_index >= max_rounds:
        emit_progress(state, "reflect", f"已达到最大取证轮数 {max_rounds}，进入报告撰写")
        state["evidence_gaps"] = []
        state["reflection"] = {"sufficient": True, "reason": f"已达最大取证轮数 {max_rounds}", "evidence_gaps": []}
        return state

    prompt = get_reflection_prompt(
        _get_query(state),
        state.get("intent_mode", "topic_research"),
        state.get("open_questions", []),
        state.get("research_notes", ""),
        state.get("citations", []),
        state.get("evidence_status", []),
        round_index,
        max_rounds,
    )
    emit_progress(state, "reflect", "正在审查证据是否足够支撑结论")
    parsed = parse_json_object(_call_llm_with_progress(state, "reflect", prompt, system=REFLECT_SYSTEM, json_object=True))

    if not parsed:
        # 解析失败时用确定性规则兜底：只要有论文的证据是 fallback 就补一轮
        fallback_gaps = [
            f"《{item.get('title', 'unknown')}》缺少属于自己的证据片段"
            for item in state.get("evidence_status", [])
            if item.get("evidence_status") == "fallback"
        ]
        parsed = {
            "sufficient": not fallback_gaps,
            "evidence_gaps": fallback_gaps,
            "open_questions": state.get("open_questions", []),
            "next_queries": [],
            "reason": "反思结果解析失败，按证据归属情况判定",
        }

    sufficient = bool(parsed.get("sufficient", True))
    gaps = [str(item).strip() for item in parsed.get("evidence_gaps", []) if str(item).strip()]
    if sufficient:
        gaps = []

    state["evidence_gaps"] = gaps
    state["open_questions"] = [str(item).strip() for item in parsed.get("open_questions", state.get("open_questions", [])) if str(item).strip()]
    next_queries = [str(item).strip() for item in parsed.get("next_queries", []) if str(item).strip()]
    reflection = {
        "round": round_index,
        "sufficient": sufficient and not gaps,
        "evidence_gaps": gaps,
        "open_questions": state["open_questions"],
        "next_queries": next_queries,
        "reason": str(parsed.get("reason", "")),
    }
    state["reflection"] = reflection
    state["working_document"] = add_reflection(dict(state.get("working_document") or {}), reflection)

    if reflection["sufficient"]:
        emit_progress(state, "reflect", f"证据充分，进入报告撰写（{reflection['reason'] or '无补充说明'}）")
    else:
        emit_progress(state, "reflect", f"证据不足，准备补充取证：{gaps}")
        if next_queries:
            state["query_batches"] = [{"label": f"gap_{index}", "query": query} for index, query in enumerate(next_queries, 1)]
            emit_progress(state, "reflect", f"下一轮检索词：{next_queries}")

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
    """逐节撰写综述。

    get_survey_section_prompt 此前是死代码 —— 功能设计了、prompt 写了，
    但从没接到 writer 上，导致大纲被硬编码、报告是单次生成。
    """
    working_document = dict(state.get("working_document") or {})
    docs = state.get("retrieved_docs", [])
    evidence_text = format_retrieved_docs(docs[:8])
    sections: list[dict[str, str]] = []

    for index, title in enumerate(outline, 1):
        emit_progress(state, "report", f"正在撰写第 {index}/{len(outline)} 节：{title}")
        body = _call_llm_with_progress(
            state,
            "report",
            get_survey_section_prompt(
                state.get("topic", ""),
                title,
                f"{notes}\n\n检索证据：\n{evidence_text}",
                working_document,
            ),
            system=SURVEY_SECTION_SYSTEM,
        ).strip()
        if not body:
            emit_progress(state, "report", f"第 {index} 节生成为空，跳过")
            continue
        sections.append({"title": title, "body": body})
    return sections


def _compose_survey(topic: str, sections: list[dict[str, str]]) -> str:
    parts = [f"# {topic} 调研报告" if topic else "# 调研报告"]
    for section in sections:
        parts.append(f"\n## {section['title']}\n\n{section['body'].strip()}")
    return "\n".join(parts).strip()


def _persist_report(state: dict[str, Any], report: str) -> str:
    """把报告写到 reports_dir（或调用方指定的 output_path），返回落盘路径。"""
    explicit = str(state.get("output_path") or "").strip()
    if not report.strip():
        return ""
    # 论文问答是逐轮对话，只有显式 --output 时才落盘；调研报告默认落盘
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
    task = state.get("task", "")
    report = state.get("final_answer") or state.get("final_report", "")

    preferences = infer_preferences(task, report, state.get("user_profile", {}), invoke_llm=_call_llm)
    merged_preferences = merge_preferences(state.get("user_profile", {}), preferences)
    state["user_profile"] = merged_preferences

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

    notes_payload = []
    if state.get("selected_papers"):
        notes_payload.extend(
            [
                {"memory_type": "interest", "content": f"研究主题 {state.get('topic', '')} 中关注 {paper.get('title', '')}"}
                for paper in state["selected_papers"][:3]
            ]
        )
    if report:
        notes_payload.append({"memory_type": "summary", "content": report[:240]})
    if not notes_payload:
        notes_payload.append({"memory_type": "summary", "content": task[:240]})

    prompt = get_memory_selection_prompt(
        state.get("topic") or state.get("initial_user_question", ""),
        notes_payload,
        report,
        state.get("user_profile", {}),
    )
    emit_progress(state, "memory", "正在准备可选长期记忆候选")
    candidates = parse_json_dicts(_call_llm_with_progress(state, "memory", prompt, system=MEMORY_SYSTEM))
    if not candidates:
        candidates = notes_payload[:3]

    state["pending_memory_candidates"] = candidates
    state["awaiting_memory_confirmation"] = True
    emit_progress(state, "memory", f"已生成 {len(candidates)} 条长期记忆候选")
    attach_memory_summary(state)
    return state
