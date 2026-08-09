from __future__ import annotations

from pathlib import Path

import app as app_module


def test_paper_qa_pipeline_returns_grounded_answer_and_citations(monkeypatch, tmp_path: Path) -> None:
    paper_path = tmp_path / "paper.md"
    paper_path.write_text(
        "# Hybrid Retrieval Study\n\n"
        "The core contribution is a hybrid retrieval pipeline that combines semantic similarity with sparse retrieval.\n"
        "The experiments show better grounding and citation quality than sparse-only retrieval.\n",
        encoding="utf-8",
    )

    import agent.nodes as nodes_module
    import memory.long_term as long_term_module
    import memory.retrieval as memory_retrieval_module

    monkeypatch.setattr(long_term_module, "get_user_preferences", lambda *args, **kwargs: {"user_id": "tester"})
    monkeypatch.setattr(memory_retrieval_module, "retrieve_relevant_long_term_memories", lambda *args, **kwargs: [])
    monkeypatch.setattr(nodes_module, "save_session_artifact", lambda *args, **kwargs: None)

    def fake_invoke(prompt: str) -> str:
        lowered = prompt.lower()
        if "搜索计划" in prompt or "search plan" in lowered:
            return '{"goal": "paper qa", "subtopics": [], "open_questions": []}'
        if "query batch" in lowered:
            return '[]'
        if "memory" in lowered and "候选" in prompt:
            return '[{"memory_type": "summary", "content": "hybrid retrieval pipeline"}]'
        if "研究笔记" in prompt or "research" in lowered:
            return "研究笔记：混合检索结合 semantic similarity 和 sparse retrieval。"
        return "最终回答：这篇论文的核心贡献是结合 semantic similarity 与 sparse retrieval 的混合检索，并提升 grounding 质量。"

    monkeypatch.setattr(nodes_module, "_invoke_llm", fake_invoke)

    final_state = app_module.run_research(
        query="这篇论文的核心贡献是什么？",
        user_id="tester",
        session_id="paper-qa-test",
        file_path=str(paper_path),
        rebuild_index=True,
        top_k=2,
        resume=False,
    )

    assert final_state["intent_mode"] == "paper_qa"
    assert final_state["retrieved_docs"]
    assert final_state["citations"]
    assert final_state["citations"][0]["source_name"] == "paper.md"
    assert "混合检索" in final_state["final_answer"] or "hybrid retrieval" in final_state["final_answer"].lower()
    assert final_state["pending_memory_candidates"]
