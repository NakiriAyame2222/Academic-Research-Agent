from __future__ import annotations

import app as app_module
from tests.benchmark_helpers import write_artifact


def test_topic_research_pipeline_builds_search_plan_selected_papers_and_survey(monkeypatch) -> None:
    import agent.nodes as nodes_module
    import memory.long_term as long_term_module
    import memory.retrieval as memory_retrieval_module

    topic = "llm agent memory"
    fake_papers = [
        {
            "title": "Memory-Augmented Agents",
            "summary": "Studies memory-augmented planning for agents.",
            "authors": ["A"],
            "published": "2026-01-01",
            "arxiv_id": "2601.00001",
            "pdf_url": "http://example.com/1.pdf",
            "entry_url": "http://arxiv.org/abs/2601.00001",
            "query": topic,
            "query_label": "broad",
        },
        {
            "title": "Long Context Agent Memory",
            "summary": "Focuses on retention and retrieval across long interactions.",
            "authors": ["B"],
            "published": "2026-02-01",
            "arxiv_id": "2602.00002",
            "pdf_url": "http://example.com/2.pdf",
            "entry_url": "http://arxiv.org/abs/2602.00002",
            "query": topic,
            "query_label": "survey",
        },
        {
            "title": "Tool-Using Agent Memory Benchmarks",
            "summary": "Provides evaluation benchmarks for agent memory systems.",
            "authors": ["C"],
            "published": "2026-03-01",
            "arxiv_id": "2603.00003",
            "pdf_url": "http://example.com/3.pdf",
            "entry_url": "http://arxiv.org/abs/2603.00003",
            "query": topic,
            "query_label": "benchmark",
        },
    ]

    monkeypatch.setattr(long_term_module, "get_user_preferences", lambda *args, **kwargs: {"user_id": "tester"})
    monkeypatch.setattr(memory_retrieval_module, "retrieve_relevant_long_term_memories", lambda *args, **kwargs: [])
    monkeypatch.setattr(nodes_module, "save_session_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        nodes_module,
        "search_academic_topic",
        lambda *args, **kwargs: {
            "queries": [topic, f"{topic} survey", f"{topic} benchmark"],
            "surveys": fake_papers[:1],
            "papers": fake_papers,
            "search_runs": [
                {"label": "broad", "query": topic, "results": fake_papers[:2]},
                {"label": "benchmark", "query": f"{topic} benchmark", "results": fake_papers[1:]},
            ],
        },
    )
    monkeypatch.setattr(
        nodes_module,
        "build_selected_papers_index",
        lambda *args, **kwargs: ("stub-persist-dir", [{**paper, "file_path": f"/tmp/{paper['arxiv_id']}.pdf"} for paper in fake_papers[:2]]),
    )
    monkeypatch.setattr(
        nodes_module,
        "retrieve_documents",
        lambda query, top_k=4, persist_dir=None: [
            {
                "page_content": "Evidence discussing memory retention and benchmark coverage.",
                "metadata": {
                    "source": "/tmp/2601.00001.pdf",
                    "source_name": "Memory-Augmented Agents.pdf",
                    "page": 3,
                    "chunk_index": 0,
                    "section_title": "Experiments",
                },
            },
            {
                "page_content": "Evidence describing long-context agent memory behavior.",
                "metadata": {
                    "source": "/tmp/2602.00002.pdf",
                    "source_name": "Long Context Agent Memory.pdf",
                    "page": 5,
                    "chunk_index": 1,
                    "section_title": "Method",
                },
            },
        ],
    )

    def fake_invoke(prompt: str) -> str:
        lowered = prompt.lower()
        if "search plan" in lowered or "搜索计划" in prompt:
            return '{"goal": "study agent memory", "subtopics": ["memory retention", "benchmarking"], "query_groups": ["broad", "survey", "benchmark"], "open_questions": ["How to evaluate retention?"], "selection_criteria": ["代表性", "相关性"], "report_outline": ["背景", "方法", "实验"]}'
        if "query batch" in lowered or "query batches" in lowered:
            return '[{"label": "broad", "query": "llm agent memory"}, {"label": "survey", "query": "llm agent memory survey"}, {"label": "benchmark", "query": "llm agent memory benchmark"}]'
        if "paper note" in lowered or "候选论文" in prompt:
            return '{"problem": "agent memory retention", "related_work": "memory augmented llm", "method": "retrieval plus compression", "experiments": "benchmark comparison", "summary": "A strong candidate for agent memory research."}'
        if "fulltext note" in lowered or "全文笔记" in prompt:
            return "全文笔记：论文强调 retention、benchmark coverage 和 grounded evidence。"
        if "memory" in lowered and "候选" in prompt:
            return '[{"memory_type": "interest", "content": "关注 agent memory benchmark"}]'
        return "最终综述：该主题聚焦 memory retention、benchmark coverage、grounded evidence，并引用代表论文展开分析。"

    monkeypatch.setattr(nodes_module, "_invoke_llm", fake_invoke)

    final_state = app_module.run_research(
        query="帮我调研一下 llm agent memory 的最新进展",
        user_id="tester",
        session_id="topic-research-test",
        rebuild_index=False,
        top_k=3,
        resume=False,
    )

    payload = {
        "topic": final_state["topic"],
        "query_batches": len(final_state["query_batches"]),
        "selected_papers": [paper.get("title", "") for paper in final_state["selected_papers"]],
        "citations": len(final_state["citations"]),
        "has_survey_artifact": bool(final_state["survey_artifact"]),
    }
    write_artifact("topic_research_metrics.json", payload)

    assert final_state["intent_mode"] == "topic_research"
    assert final_state["search_plan"]
    assert len(final_state["query_batches"]) >= 3
    assert len(final_state["selected_papers"]) >= 2
    assert final_state["survey_artifact"]
    assert final_state["survey_artifact"]["papers"]
    assert final_state["citations"]
    assert "memory" in final_state["final_answer"].lower()
