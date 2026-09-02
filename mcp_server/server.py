"""对外暴露本地论文检索能力的 MCP server。

目录名从 `mcp/` 改成 `mcp_server/`：官方 Python SDK 的包名也叫 `mcp`，
同名目录会让 `import mcp` 的解析结果取决于 sys.path 顺序。

改动前的三个问题：
1. 零调用点 —— 没有 __main__ 入口，CLI 也不调它；
2. 索引参数指错 —— data_dir 给的是整个 data/（会把 reports/ 和 downloads/ 全扫进语料），
   persist_dir 给的是 vector_store/ 根目录（CLI 主流程用的是子目录 vector_store/corpus）；
3. run() 用 try/except TypeError 猜传输方式。

现在：与 CLI 共用 papers_dir + vector_store_corpus_dir 同一份索引，
默认走 FastMCP 的 stdio，只有显式 http 传输才传 host/port。
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from config import ensure_directories, get_settings
from rag.retriever import initialize_retriever
from tools.arxiv_search import search_arxiv
from tools.file import read_text
from tools.search import search_papers

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover - optional dependency
    FastMCP = None

SERVER_NAME = "research-agent-mcp"
MAX_FILE_CHARS = 20000


class SimpleMCPServer:
    """fastmcp 未安装时的占位实现，明确告知如何启用而不是静默假装在跑。"""

    def __init__(self) -> None:
        self.name = SERVER_NAME
        self.tools = list(TOOL_NAMES)

    def run(self) -> None:
        raise RuntimeError(
            "未安装 fastmcp，无法启动 MCP server。请先执行 pip install fastmcp。\n"
            f"本 server 计划暴露的工具：{', '.join(self.tools)}"
        )


# --- 工具实现（不依赖 fastmcp，可直接被测试与其它代码调用）-------------------


def _ensure_corpus_index(rebuild: bool = False) -> str:
    """初始化与 CLI 主流程一致的语料索引，返回 persist_dir。"""
    settings = get_settings()
    ensure_directories(settings)
    persist_dir = settings["vector_store_corpus_dir"]
    initialize_retriever(
        data_dir=str(settings["papers_dir"]),
        persist_dir=persist_dir,
        chunk_size=settings["chunk_size"],
        chunk_overlap=settings["chunk_overlap"],
        rebuild=rebuild,
    )
    return str(persist_dir)


def search_local_papers(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """在本地论文库里做语义检索，返回带 page/chunk 定位的证据片段。"""
    persist_dir = _ensure_corpus_index()
    docs = search_papers(query, top_k=max(1, min(int(top_k), 20)), persist_dir=persist_dir)
    return [
        {
            "source_name": doc.get("metadata", {}).get("source_name", ""),
            "source": doc.get("metadata", {}).get("source", ""),
            "page": doc.get("metadata", {}).get("page"),
            "chunk_index": doc.get("metadata", {}).get("chunk_index", 0),
            "section_title": doc.get("metadata", {}).get("section_title", ""),
            "content": doc.get("page_content", ""),
        }
        for doc in docs
    ]


def list_local_papers() -> list[dict[str, Any]]:
    """列出 data/papers 下可检索的论文文件。"""
    settings = get_settings()
    papers_dir = Path(settings["papers_dir"])
    if not papers_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(papers_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".pdf", ".md", ".txt"}:
            results.append(
                {
                    "file_name": path.name,
                    "file_path": str(path),
                    "size_bytes": path.stat().st_size,
                    "suffix": path.suffix.lower(),
                }
            )
    return results


def read_local_file(file_path: str) -> str:
    """读取本地文件内容，限制在 data/ 目录内。"""
    settings = get_settings()
    allowed_root = Path(settings["data_dir"]).resolve()
    resolved = Path(file_path).resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"只允许读取 {allowed_root} 下的文件")
    return read_text(str(resolved))[:MAX_FILE_CHARS]


def search_arxiv_papers(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """按英文关键词在 arXiv 上检索论文。"""
    papers = search_arxiv(query, max_results=max(1, min(int(max_results), 20)))
    return [
        {
            "title": paper.get("title", ""),
            "arxiv_id": paper.get("arxiv_id", ""),
            "published": paper.get("published", ""),
            "entry_url": paper.get("entry_url", ""),
            "pdf_url": paper.get("pdf_url", ""),
            "summary": str(paper.get("summary", ""))[:600],
        }
        for paper in papers
    ]


def get_paper_notes(topic: str = "", limit: int = 10) -> list[dict[str, Any]]:
    """读取 agent 此前存进 SQLite 的论文笔记。"""
    settings = get_settings()
    db_path = Path(settings["sqlite_db_path"])
    if not db_path.exists():
        return []
    sql = "SELECT topic, title, arxiv_id, problem, method, experiments, summary, source_url, created_at FROM paper_notes"
    params: tuple[Any, ...] = ()
    if topic:
        sql += " WHERE topic LIKE ?"
        params = (f"%{topic}%",)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params = params + (max(1, min(int(limit), 50)),)

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    keys = ["topic", "title", "arxiv_id", "problem", "method", "experiments", "summary", "source_url", "created_at"]
    return [dict(zip(keys, row)) for row in rows]


TOOL_FUNCTIONS = {
    "search_local_papers": search_local_papers,
    "list_local_papers": list_local_papers,
    "read_local_file": read_local_file,
    "search_arxiv_papers": search_arxiv_papers,
    "get_paper_notes": get_paper_notes,
}
TOOL_NAMES = tuple(TOOL_FUNCTIONS)


# --- server -----------------------------------------------------------------


def create_mcp_server(rebuild_index: bool = False):
    _ensure_corpus_index(rebuild=rebuild_index)

    if FastMCP is None:
        return SimpleMCPServer()

    server = FastMCP(SERVER_NAME)
    for name, function in TOOL_FUNCTIONS.items():
        server.tool(name=name)(function)
    return server


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000, rebuild_index: bool = False) -> None:
    server = create_mcp_server(rebuild_index=rebuild_index)
    if transport == "http":
        server.run(transport="http", host=host, port=port)
        return
    server.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Research Agent MCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild the local corpus index before serving")
    args = parser.parse_args()
    run_server(transport=args.transport, host=args.host, port=args.port, rebuild_index=args.rebuild_index)


if __name__ == "__main__":
    main()
