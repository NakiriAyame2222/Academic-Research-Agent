from __future__ import annotations

from typing import Any, Callable

from agent.nodes import (
    evidence_agent,
    planner_agent,
    reflect_evidence,
    report_writer,
    research_agent,
    route_after_reflection,
    update_memory_node,
)
from agent.state import build_initial_state

from langgraph.graph import END, START, StateGraph

# 图结构：planner -> evidence_agent -> researcher -> reflect -> (回 evidence_agent | writer)
# 改动前是五条固定 add_edge 的线性链，没有条件边也没有反思/重试循环。
NODE_SEQUENCE: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = [
    ("planner", planner_agent),
    ("evidence_agent", evidence_agent),
    ("researcher", research_agent),
    ("reflect", reflect_evidence),
    ("writer", report_writer),
    ("memory_updater", update_memory_node),
]

# 静态后继表；reflect 的后继由 route_after_reflection 动态决定
STATIC_TRANSITIONS: dict[str, str] = {
    "planner": "evidence_agent",
    "evidence_agent": "researcher",
    "researcher": "reflect",
    "writer": "memory_updater",
}

MAX_NODE_STEPS = 24


def build_graph():
    nodes = dict(NODE_SEQUENCE)
    graph = StateGraph(dict)
    for name, handler in NODE_SEQUENCE:
        graph.add_node(name, handler)

    graph.add_edge(START, "planner")
    for source, target in STATIC_TRANSITIONS.items():
        graph.add_edge(source, target)
    graph.add_conditional_edges(
        "reflect",
        route_after_reflection,
        {"evidence_agent": "evidence_agent", "writer": "writer"},
    )
    graph.add_edge("memory_updater", END)
    return graph.compile().with_config(recursion_limit=MAX_NODE_STEPS)


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
