from __future__ import annotations

from pathlib import Path

from config import ensure_directories, get_settings
from rag.retriever import initialize_retriever
from tools.file import read_text
from tools.search import search_papers

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover - optional dependency
    FastMCP = None


class SimpleMCPServer:
    def __init__(self) -> None:
        self.name = "research-agent-mcp"

    def run(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        print(f"Simple MCP server placeholder running on {host}:{port}")


def create_mcp_server():
    settings = get_settings()
    ensure_directories(settings)
    initialize_retriever(
        data_dir=str(settings["data_dir"]),
        persist_dir=str(settings["vector_store_dir"]),
        chunk_size=settings["chunk_size"],
        chunk_overlap=settings["chunk_overlap"],
    )

    if FastMCP is None:
        return SimpleMCPServer()

    server = FastMCP("research-agent-mcp")

    @server.tool()
    def search_local_papers(query: str) -> list[dict]:
        return search_papers(query)

    @server.tool()
    def read_local_file(file_path: str) -> str:
        return read_text(file_path)

    return server


def search_local_papers(query: str) -> list[dict]:
    return search_papers(query)


def read_local_file(file_path: str) -> str:
    return read_text(file_path)


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_mcp_server()
    if hasattr(server, "run"):
        try:
            server.run(host=host, port=port)
        except TypeError:
            server.run()
        return
    raise RuntimeError("MCP server could not be started")
