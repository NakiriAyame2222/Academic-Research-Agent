from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable

from agent.citation_check import enforce_citations

# 按真实 stage 序列（见 note/web_stack_plan.md 已知取值）编排一段假进度。
_STAGES: list[tuple[str, str]] = [
    ("planner", "解析研究意图：latent variable reasoning in LLMs"),
    ("plan", "拟定检索计划，拆出 3 个子主题"),
    ("search", "开始 arXiv 搜索：latent variable reasoning"),
    ("select", "从 12 篇候选中选出 2 篇代表作"),
    ("tool", '第 1 步：调用 search_arxiv({"query": "latent variable reasoning"})'),
    ("tool", "第 1 步：search_arxiv 返回 12 条（2.31s）"),
    ("fulltext", "下载并切块 2 篇 PDF，构建证据池"),
    ("research", "逐篇精读并整理研究笔记"),
    ("reflect", "反思证据充分性：第 1 轮通过"),
    ("report", "逐节撰写研究报告"),
    ("memory", "抽取长期记忆候选"),
]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _canned_final_state(session_id: str) -> dict[str, Any]:
    citations = [
        {
            "id": "S1",
            "source": "arxiv:2401.00001",
            "source_name": "2401.00001",
            "paper_title": "Latent Variable Reasoning in Large Language Models",
            "arxiv_id": "2401.00001",
            "page": 3,
            "chunk_index": 2,
            "section_title": "Method",
            "excerpt": "We introduce a latent variable formulation that separates the reasoning trace from the final answer distribution.",
        },
        {
            "id": "S2",
            "source": "arxiv:2402.00042",
            "source_name": "2402.00042",
            "paper_title": "On the Faithfulness of Chain-of-Thought",
            "arxiv_id": "2402.00042",
            "page": 7,
            "chunk_index": 5,
            "section_title": "Experiments",
            "excerpt": "Our probing experiments show that intermediate steps are not always causally linked to the produced answer.",
        },
    ]
    # 正文故意放一个非法编号 [S9]、且不带 Sources 小节，
    # 让假数据完整走一遍真实的引用后处理（enforce_citations：就地标注 + 补 Sources + 审计）。
    raw_report = (
        "# Latent Variable Reasoning in LLMs：综述\n\n"
        "## 核心发现\n\n"
        "近期工作把推理轨迹建模为隐变量 [S1]，从而将推理过程与答案分布解耦。"
        "但探针实验表明中间步骤未必与最终答案存在因果关联 [S2]。\n\n"
        "## 争议\n\n"
        "关于忠实性的度量仍无共识 [S9]。\n"
    )
    final_answer, citation_audit = enforce_citations(raw_report, citations)

    return {
        "session_id": session_id,
        "intent_mode": "topic_research",
        "final_answer": final_answer,
        "final_report": final_answer,
        "survey_artifact": {
            "outline": ["核心发现", "争议", "开放问题"],
            "sections": [
                {"title": "核心发现", "summary": "隐变量建模推理轨迹。"},
                {"title": "争议", "summary": "忠实性度量无共识。"},
            ],
            "papers": [citation["paper_title"] for citation in citations],
        },
        "citations": citations,
        "citation_audit": citation_audit,
        "tool_trace": [
            {
                "step": 1,
                "tool": "search_arxiv",
                "arguments": {"query": "latent variable reasoning"},
                "result_summary": "返回 12 条候选",
                "elapsed_seconds": 2.31,
            },
            {
                "step": 2,
                "tool": "fetch_fulltext",
                "arguments": {"arxiv_id": "2401.00001"},
                "result_summary": "下载并切块 34 个片段",
                "elapsed_seconds": 18.4,
            },
        ],
        "tool_loop_degraded": False,
        "evidence_status": [
            {
                "title": "Latent Variable Reasoning in Large Language Models",
                "arxiv_id": "2401.00001",
                "evidence_status": "arxiv_id",
                "evidence_count": 5,
            },
            {
                "title": "On the Faithfulness of Chain-of-Thought",
                "arxiv_id": "",
                "evidence_status": "fallback",
                "evidence_count": 2,
            },
        ],
        "evidence_gaps": ["缺少对小模型（<3B）的对照实验"],
        "open_questions": ["隐变量表示是否可迁移到多模态推理？"],
        "research_rounds": 2,
        "selected_papers": [
            {"title": citation["paper_title"], "arxiv_id": citation["arxiv_id"]}
            for citation in citations
        ],
        "paper_notes": [
            {
                "title": citations[0]["paper_title"],
                "arxiv_id": citations[0]["arxiv_id"],
                "summary": "提出隐变量框架，将推理轨迹与答案分布解耦。",
            }
        ],
        "pending_memory_candidates": [
            {"content": "用户关注 latent variable reasoning 的忠实性问题", "memory_type": "summary"}
        ],
        # 故意塞服务器绝对路径，验证 serialize 会把它剔除。
        "report_path": r"D:\C\简历文件\learning_test_code\data\reports\fake_report.md",
        "output_path": r"D:\C\简历文件\learning_test_code\data\reports\fake_output.md",
    }


def fake_run_research(
    query: str,
    *,
    session_id: str | None = None,
    progress_event_callback: Callable[[dict[str, Any]], None] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """不碰 LLM / arXiv 的假运行：按真实 stage 序列推进度，最后返回丰满 final_state。

    - RESEARCH_AGENT_FAKE_FAIL=1，或 query 含 "__fail__" 时中途抛异常，用于覆盖 error 帧。
    - RESEARCH_AGENT_FAKE_DELAY 控制每步间隔秒数（测试里设 0，浏览器演示默认 0.25）。
    - 无副作用：不写 checkpoint、不碰 SQLite。
    """
    delay = float(os.getenv("RESEARCH_AGENT_FAKE_DELAY", "0.25") or 0)
    should_fail = _truthy(os.getenv("RESEARCH_AGENT_FAKE_FAIL")) or "__fail__" in (query or "")

    def _emit(stage: str, message: str) -> None:
        event = {
            "stage": stage,
            "message": message,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if callable(progress_event_callback):
            progress_event_callback(event)
        if callable(progress_callback):
            progress_callback(f"[{stage}] {message}")

    for index, (stage, message) in enumerate(_STAGES):
        _emit(stage, message)
        if delay:
            time.sleep(delay)
        if should_fail and index >= 2:
            raise RuntimeError("fake failure triggered（模拟 run_research 抛异常）")

    return _canned_final_state(session_id or "fake-session")
