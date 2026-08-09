from __future__ import annotations

from memory.compression import MAX_RECENT_MESSAGES, attach_memory_summary
from tests.benchmark_helpers import retention_report, write_artifact



def test_attach_memory_summary_preserves_initial_question_and_limits_recent_messages() -> None:
    messages = [{"role": "user", "content": "最初想研究什么是 RAG agent"}]
    for index in range(1, 13):
        messages.append({"role": "assistant", "content": f"回答 {index}"})
        messages.append({"role": "user", "content": f"追问 {index}"})

    state = {
        "messages": messages,
        "topic": "RAG agent",
        "paper_path": "",
        "selected_papers": [],
        "open_questions": [],
        "turns": [{"query": "最后一个追问"}],
    }

    updated = attach_memory_summary(state)

    assert updated["initial_user_question"] == "最初想研究什么是 RAG agent"
    assert len(updated["recent_messages"]) == MAX_RECENT_MESSAGES
    assert updated["recent_messages"] == messages[-MAX_RECENT_MESSAGES:]
    assert "中间阶段用户主要问题：" in updated["conversation_summary_middle"]
    assert "中间对话压缩摘要" in updated["memory_summary"]
    assert "最近问题：最后一个追问" in updated["memory_summary"]



def test_attach_memory_summary_uses_existing_initial_question_when_present() -> None:
    state = {
        "messages": [
            {"role": "user", "content": "当前问题"},
            {"role": "assistant", "content": "当前回答"},
        ],
        "initial_user_question": "保留最初目标",
        "topic": "长期任务",
        "paper_path": "paper.pdf",
        "selected_papers": [{"title": "Paper A"}, {"title": "Paper B"}],
        "open_questions": ["还缺少实验对比"],
        "turns": [{"query": "继续分析实验"}],
    }

    updated = attach_memory_summary(state)

    assert updated["initial_user_question"] == "保留最初目标"
    assert updated["conversation_summary_middle"] == ""
    assert "主题：长期任务" in updated["memory_summary"]
    assert "论文：paper.pdf" in updated["memory_summary"]
    assert "候选论文：Paper A；Paper B" in updated["memory_summary"]
    assert "待解决问题：还缺少实验对比" in updated["memory_summary"]



def test_memory_compression_benchmark_reports_retention_and_saving(memory_cases: list[dict[str, object]]) -> None:
    rows = []
    for case in memory_cases:
        state = {
            "messages": list(case["messages"]),
            "topic": case["topic"],
            "paper_path": "",
            "selected_papers": [],
            "open_questions": [case["middle_fact"]],
            "turns": list(case["turns"]),
        }
        updated = attach_memory_summary(state)
        original_chars = sum(len(item["content"]) for item in case["messages"])
        compressed_chars = len(updated["initial_user_question"]) + len(updated["conversation_summary_middle"]) + sum(
            len(item["content"]) for item in updated["recent_messages"]
        )
        rows.append(
            {
                "topic": case["topic"],
                "initial_goal_ok": case["initial_goal"] == updated["initial_user_question"],
                "middle_fact_ok": case["middle_fact"] in updated["conversation_summary_middle"] or case["middle_fact"] in updated["memory_summary"],
                "recent_fact_ok": case["recent_fact"] in updated["memory_summary"],
                "compression_ratio": (original_chars - compressed_chars) / original_chars if original_chars else 0.0,
            }
        )

    metrics = retention_report(rows)
    metrics["test_file"] = "tests/test_memory_compression.py"
    write_artifact("memory_metrics.json", metrics)

    assert metrics["samples"] >= 10
    assert metrics["initial_goal_retention"] == 1.0
    assert metrics["recent_retention"] >= 0.95
    assert metrics["middle_retention"] >= 0.70
    assert metrics["avg_token_saving_rate_proxy"] >= 0.20
