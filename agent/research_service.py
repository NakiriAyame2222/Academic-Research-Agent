from __future__ import annotations

import re
from typing import Any, Callable

from agent.json_utils import parse_json_object
from prompts import (
    NOTE_SYSTEM, REFLECT_SYSTEM, SURVEY_SECTION_SYSTEM,
    get_paper_note_prompt, get_fulltext_note_prompt,
    get_reflection_prompt, get_survey_section_prompt,
)
from rag.retriever import format_retrieved_docs
from report_templates import get_report_template

Invoke = Callable[..., str]


def _read_json(invoke: Invoke, prompt: str, system: str) -> tuple[dict[str, Any], str]:
    # 调用失败（包括认证、配置等永久错误）必须交给调用方记录失败；
    # 只容忍返回内容的解析失败，不能把请求异常当作空模型输出。
    raw = invoke(prompt, system=system, json_object=True)
    try:
        return parse_json_object(raw), raw
    except (TypeError, ValueError):
        return {}, raw


def brief_note(topic: str, paper: dict[str, Any], invoke: Invoke) -> dict[str, Any]:
    parsed, _ = _read_json(invoke, get_paper_note_prompt(topic, paper), NOTE_SYSTEM)
    note = {key: paper.get(key, "") for key in (
        "title", "published", "entry_url", "pdf_url", "query", "query_label",
    )}
    note.update(
        arxiv_id=str(paper.get("arxiv_id") or ""), authors=paper.get("authors", []),
        **{key: parsed.get(key, "") for key in ("problem", "related_work", "method", "experiments")},
        summary=parsed.get("summary", paper.get("summary", "")), selection_status="candidate",
    )
    return note


def docs_for_paper(paper: dict[str, Any], docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z]+", "", str(value or "")).lower()

    arxiv_id = normalized(paper.get("arxiv_id"))
    if arxiv_id:
        for field, label in (("arxiv_id", "arxiv_id"), ("source_name", "source_name_prefix")):
            matches = [doc for doc in docs if (
                normalized(doc.get("metadata", {}).get(field)) == arxiv_id
                if field == "arxiv_id" else arxiv_id in normalized(doc.get("metadata", {}).get(field))
            )]
            if matches:
                return matches, label
    title = str(paper.get("title") or "").strip()
    if title:
        matches = [doc for doc in docs if any(
            title in str(doc.get("metadata", {}).get(field, ""))
            for field in ("paper_title", "source_name")
        )]
        if matches:
            return matches, "title"
    return docs[:2], "fallback"


def format_fulltext_note(parsed: dict[str, Any], raw: str) -> str:
    fields = (("problem", "研究问题"), ("method", "方法"), ("experiments", "实验与结果"),
              ("strengths", "优势"), ("limitations", "局限"), ("summary", "总体结论"))
    lines = [f"{label}：{str(parsed[key]).strip()}" for key, label in fields if str(parsed.get(key, "")).strip()]
    return "\n".join(lines) or raw.strip()


def fulltext_note(topic: str, paper: dict[str, Any], docs: list[dict[str, Any]], invoke: Invoke) -> dict[str, Any]:
    parsed, raw = _read_json(invoke, get_fulltext_note_prompt(topic, paper, format_retrieved_docs(docs)), NOTE_SYSTEM)
    return {
        "title": paper.get("title", ""), "arxiv_id": str(paper.get("arxiv_id") or ""),
        **{key: parsed.get(key, "") for key in ("problem", "method", "experiments", "strengths", "limitations")},
        "summary": format_fulltext_note(parsed, raw) or "全文笔记生成失败，证据不足。",
        "citation_ids": [doc.get("metadata", {}).get("citation_id", "") for doc in docs],
        "selection_status": "selected",
    }


def review_evidence(query: str, intent: str, notes: str, citations: list[dict[str, Any]],
                    evidence_status: list[dict[str, Any]], open_questions: list[str],
                    round_index: int, max_rounds: int, invoke: Invoke) -> dict[str, Any]:
    prompt = get_reflection_prompt(query, intent, open_questions, notes, citations, evidence_status, round_index, max_rounds)
    parsed, _ = _read_json(invoke, prompt, REFLECT_SYSTEM)
    if not parsed:
        gaps = [f"《{item.get('title', 'unknown')}》缺少属于自己的证据片段"
                for item in evidence_status if item.get("evidence_status") == "fallback"]
        parsed = {"sufficient": False, "evidence_gaps": gaps or ["证据审查未返回有效结果"],
                  "open_questions": open_questions, "reason": "反思结果不可用"}

    def strings(key: str, default: list[str] | None = None) -> list[str]:
        value = parsed.get(key, default or [])
        return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []

    gaps = strings("evidence_gaps")
    sufficient = parsed.get("sufficient") is True and not gaps
    if not sufficient and not gaps:
        gaps = ["证据尚不充分，未给出具体缺口"]
    return {"round": round_index, "sufficient": sufficient, "evidence_gaps": gaps,
            "open_questions": strings("open_questions", open_questions), "next_queries": strings("next_queries"),
            "reason": str(parsed.get("reason", "")),
            "stop_reason": "sufficient" if sufficient else "round_limit" if round_index >= max_rounds else "needs_evidence"}


def write_sections(topic: str, outline: list[str], notes: str, docs: list[dict[str, Any]],
                   working_document: dict[str, Any], invoke: Invoke,
                   before_section: Callable[[int, str], None]) -> list[dict[str, str]]:
    working_document = {**working_document, "report_template": get_report_template("topic_research")}
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        topic_id = str(doc.get("metadata", {}).get("topic_id") or "")
        by_topic.setdefault(topic_id, []).append(doc)
    evidence = [doc for group in by_topic.values() for doc in group[:3]] if any(by_topic) else docs[:8]
    evidence_text = f"{notes}\n\n检索证据：\n{format_retrieved_docs(evidence)}"
    sections = []
    for index, title in enumerate(outline, 1):
        before_section(index, title)
        prompt = get_survey_section_prompt(topic, title, evidence_text, working_document)
        body = invoke(prompt, system=SURVEY_SECTION_SYSTEM).strip()
        if not body:
            raise RuntimeError(f"章节《{title}》生成内容为空")
        sections.append({"title": title, "body": body})
    return sections


def compose_report(topic: str, sections: list[dict[str, str]]) -> str:
    parts = [f"# {topic} 调研报告" if topic else "# 调研报告"]
    parts.extend(f"\n## {section['title']}\n\n{section['body'].strip()}" for section in sections)
    return "\n".join(parts).strip()
