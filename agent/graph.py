from __future__ import annotations

from typing import Any, Callable

from agent.nodes import planner_agent, rag_search, report_writer, research_agent, update_memory_node
from agent.state import build_initial_state

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - fallback when langgraph is unavailable
    END = "__end__"
    START = "__start__"
    StateGraph = None

# 当langgraph不可用的时候的降级代替
class SimpleCompiledGraph:
    def __init__(self, nodes: list[Callable[[dict[str, Any]], dict[str, Any]]]) -> None:
        self.nodes = nodes

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current_state = state
        for node in self.nodes:
            current_state = node(current_state)
        return current_state


def build_graph():
    nodes = [planner_agent, rag_search, research_agent, report_writer, update_memory_node]
    if StateGraph is None:
        return SimpleCompiledGraph(nodes)

    graph = StateGraph(dict)
    graph.add_node("planner", planner_agent)
    graph.add_node("retriever", rag_search)
    graph.add_node("researcher", research_agent)
    graph.add_node("writer", report_writer)
    graph.add_node("memory_updater", update_memory_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "retriever")
    graph.add_edge("retriever", "researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "memory_updater")
    graph.add_edge("memory_updater", END)
    return graph.compile()


def run_graph(
    query: str,
    user_id: str = "default",
    session_id: str | None = None,
    mode: str = "report",
    file_path: str | None = None,
) -> dict[str, Any]:
    app = build_graph()
    initial_state = build_initial_state(
        query=query,
        user_id=user_id,
        session_id=session_id,
        mode=mode,
        file_path=file_path,
    )
    return app.invoke(initial_state)
