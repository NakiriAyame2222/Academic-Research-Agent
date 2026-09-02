"""意图路由的唯一真源。

改动前 app.py:detect_intent_mode 与 agent/nodes.py:_infer_intent_mode 各维护一份
词表且已经不一致，且逻辑是"首次命中即决定" —— demo 里
"搜索并总结现有的关于潜变量推理的研究,返回一个报告" 因为 topic 词表没有裸"研究"、
先被 paper 词表的"总结"命中，被误判成 paper_qa，最终走了本地检索。

现在改为加权打分：强信号优先，弱信号只在没有强信号时参与比较。
"""
from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_PAPER_EXTENSIONS = {".pdf", ".txt", ".md"}

# 强 paper 信号：明确指向"手上这一篇/这一份文档"的指代或篇内定位
PAPER_STRONG_TOKENS = [
    "这篇", "这份", "本文", "文中", "该论文", "该文", "这个论文", "这篇文章", "这几篇", "这些论文",
    "this paper", "the paper", "this article", "this document", "in the paper",
    "figure", "table", "section", "ablation", "appendix", "算法步骤", "第 ", "论文摘要", "result table",
]
PAPER_STRONG_PATTERNS = [
    re.compile(r"这\s*[两二三四五六七八九十几\d]*\s*篇"),
    re.compile(r"第\s*\d+\s*[节章页]"),
    re.compile(r"\bfig(?:ure)?\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bsection\s*\d+", re.IGNORECASE),
]

# 强 topic 信号：明确指向"去找一批论文/看一个方向"
TOPIC_STRONG_TOKENS = [
    "调研", "综述", "找论文", "搜论文", "搜一下", "收集", "搜集", "最新进展", "研究方向", "研究脉络",
    "代表性论文", "代表性工作", "我想研究", "最新方法", "研究现状", "发展脉络", "文献",
    "arxiv", "survey", "literature review", "recent papers", "state of the art", "research direction",
    "topic research", "find papers", "recent work", "latest work",
]

# 弱信号：只在双方都没有强信号时参与
PAPER_WEAK_TOKENS = [
    "论文", "方法", "实验结果", "贡献", "解释", "什么", "如何", "为什么", "怎么做", "结论",
    "paper", "method", "experiment", "contribution", "dataset", "authors", "latency",
    "conclusion", "future work", "methodology", "baseline",
]
TOPIC_WEAK_TOKENS = [
    "研究", "搜索", "搜一下", "检索", "收集", "发展", "趋势", "主题", "方向", "整理一批", "有哪些工作",
    "research", "review", "overview", "latest", "trend", "topic", "papers on", "works on",
]

# 既能出现在综述也能出现在单篇问答里的动词，单独出现不足以定性
AMBIGUOUS_TOKENS = ["总结", "对比", "比较", "报告", "summarize", "summary", "compare", "report"]


def _count(tokens: list[str], lowered: str) -> int:
    return sum(1 for token in tokens if token in lowered)


def _has_strong_paper_pattern(text: str) -> bool:
    return any(pattern.search(text) for pattern in PAPER_STRONG_PATTERNS)


def looks_like_document_path(query: str) -> bool:
    candidate = query.strip().strip('"').strip("'")
    if not candidate:
        return False
    try:
        path = Path(candidate)
    except (OSError, ValueError):
        return False
    return path.suffix.lower() in SUPPORTED_PAPER_EXTENSIONS and path.exists()


def score_intent(query: str) -> dict[str, object]:
    """返回打分明细，便于测试与调试定位误判来源。"""
    lowered = (query or "").lower()

    paper_strong = _count(PAPER_STRONG_TOKENS, lowered) + (1 if _has_strong_paper_pattern(query or "") else 0)
    topic_strong = _count(TOPIC_STRONG_TOKENS, lowered)
    paper_weak = _count(PAPER_WEAK_TOKENS, lowered)
    topic_weak = _count(TOPIC_WEAK_TOKENS, lowered)
    ambiguous = _count(AMBIGUOUS_TOKENS, lowered)

    # 强信号权重远高于弱信号，避免"总结"这类模糊动词抢先定性
    paper_score = paper_strong * 10 + paper_weak
    topic_score = topic_strong * 10 + topic_weak

    return {
        "paper_score": paper_score,
        "topic_score": topic_score,
        "paper_strong": paper_strong,
        "topic_strong": topic_strong,
        "paper_weak": paper_weak,
        "topic_weak": topic_weak,
        "ambiguous": ambiguous,
    }


def classify_intent(
    query: str,
    file_path: str | None = None,
    has_active_document: bool = False,
    default: str = "topic_research",
) -> str:
    """判定 paper_qa / topic_research。锁定文件永远优先。"""
    if file_path or has_active_document:
        return "paper_qa"
    if looks_like_document_path(query):
        return "paper_qa"

    scores = score_intent(query)
    paper_score = int(scores["paper_score"])
    topic_score = int(scores["topic_score"])

    if paper_score > topic_score:
        return "paper_qa"
    if topic_score > paper_score:
        return "topic_research"
    if paper_score == 0 and topic_score == 0:
        return default
    # 完全打平：有强 paper 指代词才判 paper_qa，否则按调研处理
    return "paper_qa" if int(scores["paper_strong"]) > 0 else default
