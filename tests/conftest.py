from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


class KeywordEmbedder:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping

    def _vector_for_text(self, text: str) -> list[float]:
        lowered = text.lower()
        for keyword, vector in self.mapping.items():
            if keyword in lowered:
                return vector
        return [0.0, 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for_text(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector_for_text(text)


class FailingQueryEmbedder(KeywordEmbedder):
    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("query embedding unavailable")


@pytest.fixture
def sample_paper_docs(tmp_path: Path) -> dict[str, Any]:
    docs_dir = tmp_path / "papers"
    docs_dir.mkdir()

    attention_doc = docs_dir / "attention_note.md"
    attention_doc.write_text(
        "# Attention Complexity\n\n"
        "DOC_ATTENTION self attention complexity is quadratic O n squared.\n"
        "The analysis discusses why the computation cost grows with sequence length.\n",
        encoding="utf-8",
    )

    memory_doc = docs_dir / "memory_note.md"
    memory_doc.write_text(
        "# Memory Compression\n\n"
        "DOC_MEMORY memory compression contribution improves long context conversations.\n"
        "The key contribution is better retention of earlier goals and summaries.\n",
        encoding="utf-8",
    )

    distractor_doc = docs_dir / "distractor_note.md"
    distractor_doc.write_text(
        "# Background Discussion\n\n"
        "DOC_DISTRACTOR memory memory discussion and generic context notes.\n"
        "This file repeats some broad terms but does not answer the target questions.\n",
        encoding="utf-8",
    )

    return {
        "data_dir": docs_dir,
        "docs": {
            "attention": attention_doc,
            "memory": memory_doc,
            "distractor": distractor_doc,
        },
        "query_to_expected_source": {
            "attention complexity": attention_doc.name,
            "memory compression contribution": memory_doc.name,
        },
    }


@pytest.fixture
def benchmark_corpus(tmp_path: Path) -> dict[str, Any]:
    docs_dir = tmp_path / "benchmark_corpus"
    docs_dir.mkdir()

    documents = [
        {
            "key": "rag_eval",
            "file_name": "rag_evaluation.md",
            "title": "# RAG Evaluation Metrics",
            "body": [
                "DOC_RAG_EVAL retrieval grounded evaluation focuses on faithfulness citation coverage and answer support.",
                "The benchmark highlights attribution precision and evidence coverage for retrieval augmented generation.",
            ],
            "queries": [
                {"query": "Which file discusses citation coverage in RAG evaluation?", "relevant": ["rag_evaluation.md"], "graded_relevance": {"rag_evaluation.md": 3.0}},
                {"query": "faithfulness attribution precision benchmark", "relevant": ["rag_evaluation.md"], "graded_relevance": {"rag_evaluation.md": 3.0, "retrieval_latency.md": 1.0}},
                {"query": "evidence coverage for retrieval augmented generation", "relevant": ["rag_evaluation.md"], "graded_relevance": {"rag_evaluation.md": 3.0}},
                {"query": "RAG evaluation metrics for grounded answers", "relevant": ["rag_evaluation.md"], "graded_relevance": {"rag_evaluation.md": 3.0}},
                {"query": "citation coverage and attribution precision", "relevant": ["rag_evaluation.md"], "graded_relevance": {"rag_evaluation.md": 3.0}},
            ],
        },
        {
            "key": "agent_memory",
            "file_name": "agent_memory.md",
            "title": "# Agent Memory Compression",
            "body": [
                "DOC_AGENT_MEMORY agent memory compression keeps initial goals middle summaries and recent turns in long conversations.",
                "The design trades token cost for retention of user intent and open questions.",
            ],
            "queries": [
                {"query": "How does the corpus describe initial goals and recent turns?", "relevant": ["agent_memory.md"], "graded_relevance": {"agent_memory.md": 3.0}},
                {"query": "agent memory compression retention of user intent", "relevant": ["agent_memory.md"], "graded_relevance": {"agent_memory.md": 3.0}},
                {"query": "middle summaries in long conversations", "relevant": ["agent_memory.md"], "graded_relevance": {"agent_memory.md": 3.0}},
                {"query": "token cost versus retention of open questions", "relevant": ["agent_memory.md"], "graded_relevance": {"agent_memory.md": 3.0}},
                {"query": "which document covers conversation memory compression", "relevant": ["agent_memory.md"], "graded_relevance": {"agent_memory.md": 3.0}},
            ],
        },
        {
            "key": "planner_search",
            "file_name": "planner_search.md",
            "title": "# Planner Search Strategy",
            "body": [
                "DOC_PLANNER_SEARCH topic research starts with a search plan query batches subtopics and explicit selection criteria.",
                "A strong planner increases query diversity before paper selection.",
            ],
            "queries": [
                {"query": "What document explains query batches and selection criteria?", "relevant": ["planner_search.md"], "graded_relevance": {"planner_search.md": 3.0}},
                {"query": "topic research search plan and subtopics", "relevant": ["planner_search.md"], "graded_relevance": {"planner_search.md": 3.0}},
                {"query": "query diversity before paper selection", "relevant": ["planner_search.md"], "graded_relevance": {"planner_search.md": 3.0, "paper_selection.md": 1.0}},
                {"query": "planner document for topic research", "relevant": ["planner_search.md"], "graded_relevance": {"planner_search.md": 3.0}},
                {"query": "selection criteria in search planning", "relevant": ["planner_search.md"], "graded_relevance": {"planner_search.md": 3.0}},
            ],
        },
        {
            "key": "paper_selection",
            "file_name": "paper_selection.md",
            "title": "# Representative Paper Selection",
            "body": [
                "DOC_PAPER_SELECTION representative papers are chosen by relevance recency and experiment quality after deduplication.",
                "Selection should balance breadth with authoritative results.",
            ],
            "queries": [
                {"query": "How are representative papers chosen after deduplication?", "relevant": ["paper_selection.md"], "graded_relevance": {"paper_selection.md": 3.0}},
                {"query": "relevance recency and experiment quality", "relevant": ["paper_selection.md"], "graded_relevance": {"paper_selection.md": 3.0}},
                {"query": "balance breadth with authoritative results", "relevant": ["paper_selection.md"], "graded_relevance": {"paper_selection.md": 3.0}},
                {"query": "what document covers representative paper selection", "relevant": ["paper_selection.md"], "graded_relevance": {"paper_selection.md": 3.0}},
                {"query": "deduplication before selecting papers", "relevant": ["paper_selection.md"], "graded_relevance": {"paper_selection.md": 3.0}},
            ],
        },
        {
            "key": "citation_grounding",
            "file_name": "citation_grounding.md",
            "title": "# Citation Grounding",
            "body": [
                "DOC_CITATION source page chunk index section title and excerpt are required for grounded academic answers.",
                "Precise page references improve trust in generated reports.",
            ],
            "queries": [
                {"query": "Which metadata fields are required for grounded academic answers?", "relevant": ["citation_grounding.md"], "graded_relevance": {"citation_grounding.md": 3.0}},
                {"query": "source page chunk index section title excerpt", "relevant": ["citation_grounding.md"], "graded_relevance": {"citation_grounding.md": 3.0}},
                {"query": "precise page references improve trust", "relevant": ["citation_grounding.md"], "graded_relevance": {"citation_grounding.md": 3.0}},
                {"query": "grounded academic answers metadata", "relevant": ["citation_grounding.md"], "graded_relevance": {"citation_grounding.md": 3.0}},
                {"query": "citation grounding with page references", "relevant": ["citation_grounding.md"], "graded_relevance": {"citation_grounding.md": 3.0}},
            ],
        },
        {
            "key": "retrieval_latency",
            "file_name": "retrieval_latency.md",
            "title": "# Retrieval Latency",
            "body": [
                "DOC_RETRIEVAL_LATENCY retrieval time index build time and request counts are important efficiency metrics.",
                "Latency should be tracked alongside answer quality.",
            ],
            "queries": [
                {"query": "What efficiency metrics are mentioned for retrieval systems?", "relevant": ["retrieval_latency.md"], "graded_relevance": {"retrieval_latency.md": 3.0}},
                {"query": "retrieval time index build time request counts", "relevant": ["retrieval_latency.md"], "graded_relevance": {"retrieval_latency.md": 3.0}},
                {"query": "latency tracked alongside answer quality", "relevant": ["retrieval_latency.md"], "graded_relevance": {"retrieval_latency.md": 3.0}},
                {"query": "efficiency metrics for retrieval latency", "relevant": ["retrieval_latency.md"], "graded_relevance": {"retrieval_latency.md": 3.0, "rag_evaluation.md": 1.0}},
                {"query": "index build time and request counts", "relevant": ["retrieval_latency.md"], "graded_relevance": {"retrieval_latency.md": 3.0}},
            ],
        },
    ]

    embedder_mapping = {
        "doc_rag_eval": [1.0, 0.0, 0.0],
        "citation coverage": [1.0, 0.0, 0.0],
        "faithfulness attribution precision": [1.0, 0.0, 0.0],
        "evidence coverage": [1.0, 0.0, 0.0],
        "doc_agent_memory": [0.0, 1.0, 0.0],
        "memory compression": [0.0, 1.0, 0.0],
        "middle summaries": [0.0, 1.0, 0.0],
        "recent turns": [0.0, 1.0, 0.0],
        "doc_planner_search": [0.0, 0.0, 1.0],
        "search plan": [0.0, 0.0, 1.0],
        "query batches": [0.0, 0.0, 1.0],
        "selection criteria": [0.0, 0.0, 1.0],
        "doc_paper_selection": [1.0, 1.0, 0.0],
        "representative papers": [1.0, 1.0, 0.0],
        "deduplication": [1.0, 1.0, 0.0],
        "experiment quality": [1.0, 1.0, 0.0],
        "doc_citation": [1.0, 0.0, 1.0],
        "source page chunk index": [1.0, 0.0, 1.0],
        "precise page references": [1.0, 0.0, 1.0],
        "grounded academic answers": [1.0, 0.0, 1.0],
        "doc_retrieval_latency": [0.0, 1.0, 1.0],
        "retrieval time": [0.0, 1.0, 1.0],
        "index build time": [0.0, 1.0, 1.0],
        "request counts": [0.0, 1.0, 1.0],
    }

    retrieval_cases: list[dict[str, Any]] = []
    for document in documents:
        file_path = docs_dir / document["file_name"]
        file_path.write_text(
            document["title"] + "\n\n" + "\n".join(document["body"]) + "\n",
            encoding="utf-8",
        )
        for case in document["queries"]:
            retrieval_cases.append(
                {
                    "query": case["query"],
                    "relevant_sources": case["relevant"],
                    "graded_relevance": case["graded_relevance"],
                    "category": document["key"],
                }
            )

    citation_cases = [
        {
            "query": "citation coverage and attribution precision",
            "source_name": "rag_evaluation.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "citation coverage",
        },
        {
            "query": "middle summaries in long conversations",
            "source_name": "agent_memory.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "middle summaries",
        },
        {
            "query": "selection criteria in search planning",
            "source_name": "planner_search.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "selection criteria",
        },
        {
            "query": "relevance recency and experiment quality",
            "source_name": "paper_selection.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "experiment quality",
        },
        {
            "query": "citation grounding with page references",
            "source_name": "citation_grounding.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "page references",
        },
        {
            "query": "index build time and request counts",
            "source_name": "retrieval_latency.md",
            "page": None,
            "chunk_index": 0,
            "excerpt_fragment": "request counts",
        },
    ]

    return {
        "data_dir": docs_dir,
        "documents": documents,
        "retrieval_cases": retrieval_cases,
        "citation_cases": citation_cases,
        "embedder_mapping": embedder_mapping,
    }


@pytest.fixture
def keyword_embedder() -> KeywordEmbedder:
    return KeywordEmbedder(
        {
            "attention complexity": [1.0, 0.0, 0.0],
            "doc_attention": [1.0, 0.0, 0.0],
            "memory compression contribution": [0.0, 1.0, 0.0],
            "doc_memory": [0.0, 1.0, 0.0],
            "doc_distractor": [0.0, 0.0, 1.0],
        }
    )


@pytest.fixture
def benchmark_embedder(benchmark_corpus: dict[str, Any]) -> KeywordEmbedder:
    return KeywordEmbedder(benchmark_corpus["embedder_mapping"])


@pytest.fixture
def memory_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index in range(1, 11):
        topic = f"topic-{index}"
        initial_goal = f"我想系统研究 {topic} 的评测方法"
        messages = [{"role": "user", "content": initial_goal}]
        for turn in range(1, 13):
            messages.append({"role": "assistant", "content": f"回答 {turn}：讨论 {topic} 的阶段 {turn}"})
            user_text = f"追问 {turn}：补充 {topic} 的事实 {turn}"
            if turn == 4:
                user_text = f"追问 {turn}：请记住中期事实 {topic} 需要 citation grounding"
            if turn == 12:
                user_text = f"追问 {turn}：最近事实 {topic} 需要 recent evidence"
            messages.append({"role": "user", "content": user_text})
        cases.append(
            {
                "topic": topic,
                "initial_goal": initial_goal,
                "middle_fact": f"中期事实 {topic} 需要 citation grounding",
                "recent_fact": f"最近事实 {topic} 需要 recent evidence",
                "messages": messages,
                "turns": [{"query": f"最近事实 {topic} 需要 recent evidence"}],
            }
        )
    return cases
