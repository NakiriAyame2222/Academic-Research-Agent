from __future__ import annotations

from pathlib import Path

from rag.loader import _build_document
from rag.retriever import build_citations, clear_vector_store_cache, initialize_retriever, retrieve_documents
from tests.benchmark_helpers import citation_report, write_artifact


def test_build_citations_preserves_source_page_and_chunk_metadata() -> None:
    docs = [
        {
            "page_content": "Evidence paragraph about hybrid retrieval.",
            "metadata": {
                "source": "D:/papers/hybrid.pdf",
                "source_name": "hybrid.pdf",
                "page": 5,
                "chunk_index": 2,
                "section_title": "Experiments",
            },
        }
    ]

    citations = build_citations(docs)

    assert citations == [
        {
            "id": "S1",
            "source": "D:/papers/hybrid.pdf",
            "source_name": "hybrid.pdf",
            "page": 5,
            "chunk_index": 2,
            "section_title": "Experiments",
            "excerpt": "Evidence paragraph about hybrid retrieval.",
        }
    ]


def test_build_document_normalizes_zero_based_page_numbers() -> None:
    document = _build_document(
        "Page level content",
        "D:/papers/sample.pdf",
        {
            "page": 0,
            "source_name": "sample.pdf",
            "chunk_index": 0,
        },
    )

    assert document["metadata"]["page"] == 1
    assert document["metadata"]["source_name"] == "sample.pdf"


def test_citation_accuracy_benchmark_reports_precision_and_page_accuracy(benchmark_corpus: dict[str, object], benchmark_embedder, monkeypatch, tmp_path: Path) -> None:
    import rag.retriever as retriever_module

    data_dir = Path(benchmark_corpus["data_dir"])
    persist_dir = tmp_path / "citation_store"
    monkeypatch.setattr(retriever_module, "get_embedder", lambda: benchmark_embedder)
    clear_vector_store_cache()
    initialize_retriever(data_dir=str(data_dir), persist_dir=persist_dir, rebuild=True)

    rows = []
    for case in benchmark_corpus["citation_cases"]:
        docs = retrieve_documents(case["query"], top_k=1, persist_dir=persist_dir)
        assert docs
        citation = build_citations(docs)[0]
        rows.append({"query": case["query"], "expected": case, "predicted": citation})

    metrics = citation_report(rows)
    metrics["test_file"] = "tests/test_citation_accuracy.py"
    write_artifact("citation_metrics.json", metrics)

    assert metrics["samples"] >= 6
    assert metrics["citation_precision"] >= 0.85
    assert metrics["page_accuracy"] >= 0.90
    assert metrics["exact_match_rate"] >= 0.80
