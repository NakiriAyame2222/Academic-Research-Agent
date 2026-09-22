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

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - fallback when langgraph is unavailable
    END = "__end__"
    START = "__start__"
    StateGraph = None

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


class SimpleCompiledGraph:
    """langgraph 不可用时的降级实现。

    改动前只是"按列表顺序跑一遍"，没法表达 reflect 的条件回边；
    现在是按节点名驱动的小状态机，与 langgraph 版本行为一致。
    """

    def __init__(
        self,
        nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        entry: str,
        static_transitions: dict[str, str],
        conditional: dict[str, Callable[[dict[str, Any]], str]],
        terminal: str,
    ) -> None:
        self.nodes = nodes
        self.entry = entry
        self.static_transitions = static_transitions
        self.conditional = conditional
        self.terminal = terminal

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current_state = state
        current_name: str | None = self.entry
        steps = 0
        while current_name is not None:
            steps += 1
            if steps > MAX_NODE_STEPS:
                raise RuntimeError(f"graph exceeded {MAX_NODE_STEPS} node executions; possible routing loop")
            current_state = self.nodes[current_name](current_state)
            if current_name == self.terminal:
                break
            router = self.conditional.get(current_name)
            current_name = router(current_state) if router else self.static_transitions.get(current_name)
        return current_state


def build_graph():
    nodes = dict(NODE_SEQUENCE)
    conditional = {"reflect": route_after_reflection}

    if StateGraph is None:
        return SimpleCompiledGraph(
            nodes=nodes,
            entry="planner",
            static_transitions=STATIC_TRANSITIONS,
            conditional=conditional,
            terminal="memory_updater",
        )

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
