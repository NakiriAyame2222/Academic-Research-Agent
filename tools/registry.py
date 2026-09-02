"""可被 LLM 自主调用的工具注册表。

改动前 tools/ 下三个模块只是普通 Python 函数，由 agent/nodes.py 在固定位置
硬编码调用（`if intent_mode == "topic_research": ... else: ...`），LLM 无权决定
"这次不用搜 arXiv"或"证据不够再搜一轮"。

这里把它们包装成带 JSON Schema 的工具声明，交给 OpenAI 兼容的 tools 参数，
真正让模型决定调用哪个、调几轮、什么时候停。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import get_settings
from rag.retriever import retrieve_documents
from tools.arxiv_search import search_arxiv, search_surveys
from tools.file import read_text
from tools.search import build_selected_papers_index, search_papers

MAX_TOOL_RESULT_CHARS = 4000


@dataclass
class ToolContext:
    """工具执行期间的共享上下文，工具产出会累积在这里供节点读取。"""

    session_id: str = "default"
    topic: str = ""
    query: str = ""
    top_k: int = 4
    file_path: str | None = None
    corpus_persist_dir: str | None = None
    fulltext_persist_dir: str | None = None
    rebuild: bool = False
    progress: Callable[[str], None] | None = None
    papers: list[dict[str, Any]] = field(default_factory=list)
    surveys: list[dict[str, Any]] = field(default_factory=list)
    search_runs: list[dict[str, Any]] = field(default_factory=list)
    downloaded: list[dict[str, Any]] = field(default_factory=list)
    docs: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    finish_reason: str = ""

    def emit(self, message: str) -> None:
        if self.progress:
            self.progress(message)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Any]

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _trim_paper(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": paper.get("title", ""),
        "arxiv_id": paper.get("arxiv_id", ""),
        "published": paper.get("published", ""),
        "summary": str(paper.get("summary", ""))[:400],
    }


def _trim_doc(doc: dict[str, Any]) -> dict[str, Any]:
    metadata = doc.get("metadata", {})
    return {
        "source_name": metadata.get("source_name", ""),
        "arxiv_id": metadata.get("arxiv_id", ""),
        "page": metadata.get("page"),
        "chunk_index": metadata.get("chunk_index", 0),
        "section_title": metadata.get("section_title", ""),
        "excerpt": str(doc.get("page_content", ""))[:400],
    }


# --- handlers ---------------------------------------------------------------


def _handle_search_arxiv(context: ToolContext, arguments: dict[str, Any]) -> Any:
    query = str(arguments.get("query") or context.topic or context.query).strip()
    if not query:
        return {"error": "query 不能为空"}
    max_results = max(1, min(int(arguments.get("max_results") or context.top_k or 5), 10))
    label = str(arguments.get("label") or "agent")

    results = search_arxiv(query, max_results=max_results, progress_callback=context.progress)
    for paper in results:
        paper.setdefault("query", query)
        paper["query_label"] = label
    context.papers.extend(results)
    context.search_runs.append({"label": label, "query": query, "results": results})
    return {"query": query, "count": len(results), "papers": [_trim_paper(paper) for paper in results]}


def _handle_search_surveys(context: ToolContext, arguments: dict[str, Any]) -> Any:
    topic = str(arguments.get("topic") or context.topic or context.query).strip()
    if not topic:
        return {"error": "topic 不能为空"}
    max_results = max(1, min(int(arguments.get("max_results") or 3), 10))
    results = search_surveys(topic, max_results=max_results, progress_callback=context.progress)
    context.surveys.extend(results)
    return {"topic": topic, "count": len(results), "surveys": [_trim_paper(paper) for paper in results]}


def _handle_search_local(context: ToolContext, arguments: dict[str, Any]) -> Any:
    query = str(arguments.get("query") or context.query).strip()
    if not query:
        return {"error": "query 不能为空"}
    top_k = max(1, min(int(arguments.get("top_k") or context.top_k or 4), 12))
    docs = search_papers(
        query,
        top_k=top_k,
        file_path=context.file_path,
        rebuild=context.rebuild,
        persist_dir=context.corpus_persist_dir,
    )
    context.docs = docs
    return {"query": query, "count": len(docs), "chunks": [_trim_doc(doc) for doc in docs]}


def _handle_fetch_fulltext(context: ToolContext, arguments: dict[str, Any]) -> Any:
    requested = arguments.get("arxiv_ids") or []
    if isinstance(requested, str):
        requested = [item.strip() for item in requested.split(",") if item.strip()]
    wanted = {str(item).strip() for item in requested if str(item).strip()}

    pool = context.papers or []
    if wanted:
        targets = [paper for paper in pool if str(paper.get("arxiv_id", "")).strip() in wanted]
    else:
        targets = pool[: max(1, min(int(arguments.get("limit") or 3), 5))]
    if not targets:
        return {"error": "没有匹配的候选论文，请先调用 search_arxiv", "available": [_trim_paper(p) for p in pool[:8]]}

    persist_dir, downloaded = build_selected_papers_index(
        context.session_id,
        targets,
        rebuild=True,
        progress_callback=context.progress,
    )
    context.fulltext_persist_dir = persist_dir
    context.downloaded = downloaded
    return {
        "indexed_papers": [{"title": item.get("title", ""), "arxiv_id": item.get("arxiv_id", "")} for item in downloaded],
        "count": len(downloaded),
    }


def _handle_retrieve_fulltext(context: ToolContext, arguments: dict[str, Any]) -> Any:
    if not context.fulltext_persist_dir:
        return {"error": "还没有全文索引，请先调用 fetch_paper_fulltext"}
    query = str(arguments.get("query") or context.query or context.topic).strip()
    top_k = max(1, min(int(arguments.get("top_k") or 6), 20))
    docs = retrieve_documents(query, top_k=top_k, persist_dir=context.fulltext_persist_dir)
    context.docs = docs
    return {"query": query, "count": len(docs), "chunks": [_trim_doc(doc) for doc in docs]}


def _handle_read_local_file(context: ToolContext, arguments: dict[str, Any]) -> Any:
    file_path = str(arguments.get("file_path") or context.file_path or "").strip()
    if not file_path:
        return {"error": "file_path 不能为空"}

    allowed_root = Path(get_settings()["data_dir"]).resolve()
    try:
        resolved = Path(file_path).resolve()
    except OSError as exc:
        return {"error": f"路径无效：{exc}"}

    locked_document = Path(context.file_path).resolve() if context.file_path else None
    within_data_dir = resolved == allowed_root or allowed_root in resolved.parents
    if not within_data_dir and resolved != locked_document:
        return {"error": f"只允许读取 {allowed_root} 下的文件或当前锁定文档"}

    try:
        return {"file_path": str(resolved), "content": read_text(str(resolved))[:MAX_TOOL_RESULT_CHARS]}
    except OSError as exc:
        return {"error": f"读取失败：{exc}"}


def _handle_finish(context: ToolContext, arguments: dict[str, Any]) -> Any:
    context.finished = True
    context.finish_reason = str(arguments.get("reason") or "证据已足够")
    return {"finished": True, "reason": context.finish_reason}


# --- registry ---------------------------------------------------------------


TOPIC_RESEARCH_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="search_arxiv",
        description="按英文关键词在 arXiv 上检索论文。需要覆盖多个角度时可多次调用（broad / survey / method / benchmark / application）。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "英文检索关键词，不要直接用中文原句"},
                "max_results": {"type": "integer", "description": "本次返回条数，1-10", "default": 5},
                "label": {"type": "string", "description": "这一轮的角度标签，如 broad/survey/method"},
            },
            "required": ["query"],
        },
        handler=_handle_search_arxiv,
    ),
    ToolSpec(
        name="search_surveys",
        description="专门检索某个主题的综述/教程类论文，用于快速建立领域全貌。",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "英文主题词"},
                "max_results": {"type": "integer", "default": 3},
            },
            "required": ["topic"],
        },
        handler=_handle_search_surveys,
    ),
    ToolSpec(
        name="fetch_paper_fulltext",
        description="下载指定候选论文的 PDF 全文并建立可检索索引。只对真正要精读的代表论文调用。",
        parameters={
            "type": "object",
            "properties": {
                "arxiv_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要精读的论文 arxiv_id 列表，取自 search_arxiv 的返回",
                },
                "limit": {"type": "integer", "description": "不指定 arxiv_ids 时取前 N 篇", "default": 3},
            },
        },
        handler=_handle_fetch_fulltext,
    ),
    ToolSpec(
        name="retrieve_from_fulltext",
        description="在已建立的全文索引里检索关键证据片段，返回带 page/chunk 定位的原文。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要找的证据描述"},
                "top_k": {"type": "integer", "default": 6},
            },
            "required": ["query"],
        },
        handler=_handle_retrieve_fulltext,
    ),
    ToolSpec(
        name="finish",
        description="证据已经足够支撑写报告时调用，结束取证阶段。",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "为什么认为证据已足够"}},
        },
        handler=_handle_finish,
    ),
]


PAPER_QA_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="search_local_papers",
        description="在本地论文库/当前锁定文档里做语义检索，返回带 page/chunk 定位的证据片段。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题，可先做指代消解"},
                "top_k": {"type": "integer", "default": 4},
            },
            "required": ["query"],
        },
        handler=_handle_search_local,
    ),
    ToolSpec(
        name="read_local_file",
        description="读取一个本地文本/markdown 文件的内容（限 data/ 目录或当前锁定文档）。",
        parameters={
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
        handler=_handle_read_local_file,
    ),
    ToolSpec(
        name="finish",
        description="证据已经足够回答问题时调用，结束取证阶段。",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
        },
        handler=_handle_finish,
    ),
]


def get_toolset(intent_mode: str) -> list[ToolSpec]:
    return TOPIC_RESEARCH_TOOLS if intent_mode == "topic_research" else PAPER_QA_TOOLS


def to_openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [spec.to_openai_tool() for spec in specs]


def dispatch(specs: list[ToolSpec], name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
    for spec in specs:
        if spec.name == name:
            try:
                return spec.handler(context, arguments or {})
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": f"未知工具：{name}", "available": [spec.name for spec in specs]}


def serialize_result(result: Any) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"
    return text
