from __future__ import annotations

import json
from typing import Any

from agent.progress import emit_progress
from agent.research_artifacts import (
    add_candidate_paper_notes,
    add_fulltext_notes,
    add_report_outline,
    add_search_run,
    attach_final_survey,
    create_working_document,
    dedupe_papers,
    mark_selected_papers,
    select_top_papers,
)
from config import get_llm, get_settings
from memory.compression import attach_memory_summary
from memory.long_term import (
    extract_preferences_from_task,
    get_user_preferences,
    merge_preferences,
    save_paper_note,
    save_session_artifact,
)
from memory.retrieval import retrieve_relevant_long_term_memories
from prompts import (
    get_answer_prompt,
    get_fulltext_note_prompt,
    get_memory_selection_prompt,
    get_paper_note_prompt,
    get_planner_prompt,
    get_query_batch_prompt,
    get_query_rewrite_prompt,
    get_research_prompt,
    get_search_plan_prompt,
)
from rag.retriever import build_citations, format_retrieved_docs, retrieve_documents
from skills.loader import get_skill_steps, load_all_skills, select_skill
from tools.search import build_selected_papers_index, search_academic_topic, search_papers


SURVEY_ARTIFACT_TYPE = "survey_artifact"
WORKING_DOCUMENT_ARTIFACT_TYPE = "working_document"



def _invoke_llm(prompt: str) -> str:
    llm = get_llm()
    response = llm.invoke(prompt)
    if hasattr(response, "content"):
        return str(response.content)
    if hasattr(response, "text"):
        return str(response.text)
    return str(response)



def _get_query(state: dict[str, Any]) -> str:
    return state.get("current_query") or state.get("task", "")



def _infer_intent_mode(state: dict[str, Any], query: str) -> str:
    if state.get("paper_path") or state.get("active_document", {}).get("file_path"):
        return "paper_qa"
    lowered = query.lower()
    if any(token in lowered for token in [
        "arxiv", "survey", "review", "调研", "综述", "找论文", "topic", "research", "主题",
        "overview", "recent papers", "latest", "state of the art", "research direction", "研究方向", "研究脉络", "代表性论文",
        "发展", "收集", "我想研究", "最新方法",
    ]):
        return "topic_research"
    if any(token in lowered for token in [
        "论文", "这篇", "这些论文", "方法", "实验结果", "贡献", "总结", "解释", "什么", "如何", "为什么", "对比",
        "this paper", "the paper", "section", "figure", "table", "ablation", "conclusion", "future work", "methodology",
        "dataset", "authors", "latency", "result table", "算法步骤", "图", "第 ", "怎么做",
    ]):
        return "paper_qa"
    return state.get("intent_mode", "topic_research")



def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, json.JSONDecodeError):
        pass
    return {}



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
    rewritten = _invoke_llm(prompt).strip()
    if rewritten and rewritten != query:
        emit_progress(state, "rewrite", f"问题改写完成：{rewritten}")
    return rewritten or query



def _build_topic_search_plan(state: dict[str, Any], query: str, user_profile: dict[str, Any]) -> dict[str, Any]:
    emit_progress(state, "plan", f"正在为主题生成搜索计划：{query}")
    raw = _invoke_llm(get_search_plan_prompt(query, user_profile, state.get("long_term_memories", [])))
    plan = _parse_json_object(raw)
    if not plan:
        plan = {
            "goal": f"研究主题 {query} 的核心问题、方法、实验和未来方向",
            "subtopics": [query],
            "query_groups": ["broad", "survey", "method", "benchmark", "application"],
            "open_questions": [],
            "selection_criteria": ["代表性", "相关性", "实验充分性"],
            "report_outline": ["背景", "代表方法", "实验对比", "研究空白与未来方向"],
        }
    emit_progress(state, "plan", f"搜索计划完成，子主题数：{len(plan.get('subtopics', [])) or 1}")
    return plan



def _generate_query_batches(topic: str, search_plan: dict[str, Any], state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if state is not None:
        emit_progress(state, "plan", "正在生成分轮搜索 query batches")
    raw = _invoke_llm(get_query_batch_prompt(topic, search_plan))
    try:
        parsed = json.loads(raw)
        batches = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("query"):
                    batches.append({"label": str(item.get("label", item["query"])), "query": str(item["query"])})
        if batches:
            normalized: list[dict[str, Any]] = []
            seen_queries: set[str] = set()
            for item in batches:
                query_text = " ".join(str(item.get("query", "")).split())
                if not query_text:
                    continue
                lowered = query_text.lower()
                if query_text == topic or lowered in {f"{topic.lower()} survey", f"{topic.lower()} review", f"{topic.lower()} overview", f"{topic.lower()} tutorial"}:
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
    except (TypeError, json.JSONDecodeError):
        pass
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
    raw = _invoke_llm(prompt)
    parsed = _parse_json_object(raw)
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
        state.get("topic", ""),
        str(settings["sqlite_db_path"]),
    )
    skills = load_all_skills(str(settings["skills_dir"]))
    selected_skill = select_skill(query, skills)

    state.setdefault("initial_user_question", state.get("initial_user_question") or query)

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
    state["memory_summary"] = _invoke_llm(planner_prompt)

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



def rag_search(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = _get_query(state)
    retrieval_scope = dict(state.get("retrieval_scope", {}))
    file_path = retrieval_scope.get("file_path") or state.get("active_document", {}).get("file_path") or None
    rebuild = bool(retrieval_scope.get("rebuild", False))
    top_k = int(retrieval_scope.get("top_k") or settings["default_top_k"])
    rewritten_query = _rewrite_query(state, query)

    if state.get("intent_mode") == "topic_research":
        emit_progress(state, "search", "进入 topic discovery 阶段")
        search_bundle = search_academic_topic(
            state.get("topic", ""),
            max_results=max(3, top_k),
            query_batches=state.get("query_batches") or [{"label": "default", "query": rewritten_query}],
            progress_callback=lambda msg: emit_progress(state, "search", msg),
        )
        papers = dedupe_papers(search_bundle.get("papers", []))
        emit_progress(state, "search", f"候选论文去重后剩余 {len(papers)} 篇")
        working_document = dict(state.get("working_document") or {})
        working_document.setdefault("topic", state.get("topic", ""))
        for run in search_bundle.get("search_runs", []):
            working_document = add_search_run(working_document, run.get("query", ""), run.get("results", []), label=run.get("label", ""))

        brief_notes = [_brief_note_from_paper(state.get("topic", ""), paper, state) for paper in papers[:8]]
        working_document = add_candidate_paper_notes(working_document, brief_notes)
        emit_progress(state, "notes", f"已生成 {len(brief_notes)} 篇候选论文简要笔记")
        selected_papers = select_top_papers(state.get("topic", ""), papers, limit=max(3, min(5, top_k)))
        working_document = mark_selected_papers(working_document, selected_papers)
        state["selected_papers"] = selected_papers
        state["paper_candidates"] = papers
        state["paper_notes"] = brief_notes
        emit_progress(state, "select", f"已选出 {len(selected_papers)} 篇代表论文")

        if selected_papers:
            persist_dir, downloaded = build_selected_papers_index(
                state.get("session_id", state.get("user_id", "default")),
                selected_papers,
                rebuild=rebuild,
                progress_callback=lambda msg: emit_progress(state, "fulltext", msg),
            )
            retrieval_scope["persist_dir"] = persist_dir
            retrieval_scope["downloads"] = downloaded
            state["retrieval_scope"] = retrieval_scope
            emit_progress(state, "fulltext", f"开始基于全文索引检索关键证据：{rewritten_query or query}")
            fulltext_docs = retrieve_documents(rewritten_query or query, top_k=max(6, top_k), persist_dir=persist_dir)
            state["selected_paper_fulltext_docs"] = fulltext_docs
            state["retrieved_docs"] = fulltext_docs
            emit_progress(state, "fulltext", f"全文证据检索完成，命中 {len(fulltext_docs)} 个片段")
        else:
            state["selected_paper_fulltext_docs"] = []
            state["retrieved_docs"] = []
            emit_progress(state, "fulltext", "未选出代表论文，跳过全文阶段")

        state["citations"] = build_citations(state.get("retrieved_docs", []))
        state["working_document"] = working_document
        state["survey_candidates"] = search_bundle.get("surveys", [])
        attach_memory_summary(state)
        return state

    emit_progress(state, "search", f"开始检索本地论文证据：{rewritten_query or query}")
    doc_results = search_papers(
        rewritten_query or query,
        top_k=top_k,
        file_path=file_path,
        rebuild=rebuild,
        persist_dir=retrieval_scope.get("persist_dir"),
    )
    state["retrieved_docs"] = doc_results
    state["citations"] = build_citations(doc_results)
    emit_progress(state, "search", f"检索完成，命中 {len(doc_results)} 个片段")
    attach_memory_summary(state)
    return state



def research_agent(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = _get_query(state)
    docs = state.get("retrieved_docs", [])
    docs_text = format_retrieved_docs(docs[: max(4, int(state.get("retrieval_scope", {}).get("top_k") or settings["default_top_k"]))])
    if state.get("intent_mode") == "topic_research":
        emit_progress(state, "research", "进入全文深读阶段")
        selected_papers = state.get("selected_papers", [])
        fulltext_notes = []
        for paper in selected_papers[:3]:
            emit_progress(state, "research", f"正在阅读代表论文：{paper.get('title', 'unknown')}")
            paper_docs = [doc for doc in docs if paper.get("title") and paper.get("title") in str(doc.get("metadata", {}).get("source_name", ""))]
            if not paper_docs:
                paper_docs = docs[:2]
            evidence_text = format_retrieved_docs(paper_docs[:3])
            prompt = get_fulltext_note_prompt(state.get("topic", ""), paper, evidence_text)
            note_text = _invoke_llm(prompt)
            fulltext_notes.append({
                "title": paper.get("title", ""),
                "arxiv_id": str(paper.get("arxiv_id", "") or ""),
                "summary": note_text,
                "selection_status": "selected",
            })
        state["paper_notes"] = fulltext_notes
        state["research_notes"] = "\n\n".join(note.get("summary", "") for note in fulltext_notes if note.get("summary")) or docs_text
        working_document = dict(state.get("working_document") or {})
        working_document = add_fulltext_notes(working_document, fulltext_notes)
        state["working_document"] = working_document
        emit_progress(state, "research", f"全文笔记整理完成，共 {len(fulltext_notes)} 篇")
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
        )
        state["research_notes"] = _invoke_llm(prompt)
        emit_progress(state, "research", "研究笔记生成完成")

    attach_memory_summary(state)
    return state



def report_writer(state: dict[str, Any]) -> dict[str, Any]:
    query = _get_query(state)
    notes = state.get("research_notes", "") or format_retrieved_docs(state.get("retrieved_docs", []))
    active_document = state.get("active_document", {})
    survey_artifact = state.get("survey_artifact") or {}

    if state.get("intent_mode") == "topic_research":
        emit_progress(state, "report", "开始整理综述大纲")
        outline = [
            "背景与核心问题",
            "代表方法与关键贡献",
            "实验与结果对比",
            "研究空白与未来方向",
        ]
        working_document = dict(state.get("working_document") or {})
        working_document = add_report_outline(working_document, outline)
        state["working_document"] = working_document
        emit_progress(state, "report", "开始生成最终调研报告")
        prompt = get_answer_prompt(
            query,
            notes,
            state.get("user_profile", {}),
            state.get("mode", "report"),
            state.get("initial_user_question", ""),
            state.get("conversation_summary_middle", ""),
            state.get("recent_messages", state.get("messages", [])),
            active_document,
            survey_artifact={"outline": outline},
        )
        final_answer = _invoke_llm(prompt)
        survey_artifact = {
            "topic": state.get("topic", ""),
            "outline": outline,
            "full_text": final_answer,
            "papers": [paper.get("title", "") for paper in state.get("selected_papers", [])],
            "citations": state.get("citations", []),
        }
        state["survey_artifact"] = survey_artifact
        state["final_report"] = final_answer
        state["final_answer"] = final_answer
        state["final_synthesis"] = final_answer
        state["working_document"] = attach_final_survey(working_document, survey_artifact)
        emit_progress(state, "report", "调研报告生成完成")
    else:
        emit_progress(state, "report", "开始生成回答")
        prompt = get_answer_prompt(
            query,
            notes,
            state.get("user_profile", {}),
            state.get("mode", "report"),
            state.get("initial_user_question", ""),
            state.get("conversation_summary_middle", ""),
            state.get("recent_messages", state.get("messages", [])),
            active_document,
            survey_artifact,
        )
        final_answer = _invoke_llm(prompt)
        state["final_answer"] = final_answer
        state["final_report"] = final_answer
        state["final_synthesis"] = final_answer
        emit_progress(state, "report", "回答生成完成")

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



def update_memory_node(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    task = state.get("task", "")
    report = state.get("final_answer") or state.get("final_report", "")
    preferences = extract_preferences_from_task(task, report)
    merged_preferences = merge_preferences(state.get("user_profile", {}), preferences)
    state["user_profile"] = merged_preferences

    if state.get("intent_mode") == "topic_research" and state.get("selected_papers"):
        for paper in state["selected_papers"][:3]:
            save_paper_note(
                {
                    "session_id": state.get("session_id", state.get("user_id", "default")),
                    "topic": state.get("topic", ""),
                    "title": paper.get("title", ""),
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "problem": paper.get("summary", "")[:200],
                    "related_work": "",
                    "method": "",
                    "experiments": "",
                    "summary": paper.get("summary", ""),
                    "source_url": paper.get("entry_url", ""),
                },
                str(settings["sqlite_db_path"]),
            )

    notes_payload = []
    if state.get("selected_papers"):
        notes_payload.extend(
            [{"memory_type": "interest", "content": f"研究主题 {state.get('topic', '')} 中关注 {paper.get('title', '')}"} for paper in state["selected_papers"][:3]]
        )
    if report:
        notes_payload.append({"memory_type": "summary", "content": report[:240]})
    if not notes_payload:
        notes_payload.append({"memory_type": "summary", "content": task[:240]})

    prompt = get_memory_selection_prompt(state.get("topic") or state.get("initial_user_question", ""), notes_payload, report, state.get("user_profile", {}))
    emit_progress(state, "memory", "正在准备可选长期记忆候选")
    raw = _invoke_llm(prompt)
    try:
        parsed_items = json.loads(raw)
        candidates = [item for item in parsed_items if isinstance(item, dict)]
    except (TypeError, json.JSONDecodeError):
        candidates = notes_payload[:3]

    if not candidates:
        candidates = notes_payload[:3]
    state["pending_memory_candidates"] = candidates
    state["awaiting_memory_confirmation"] = True
    emit_progress(state, "memory", f"已生成 {len(candidates)} 条长期记忆候选")
    attach_memory_summary(state)
    return state
