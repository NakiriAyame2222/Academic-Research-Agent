from __future__ import annotations

from pathlib import Path

from rag.retriever import clear_vector_store_cache, initialize_retriever, retrieve_documents
from tests.benchmark_helpers import average_metric, load_json_fixture, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank, write_artifact


def _collect_metrics(cases: list[dict], persist_dir: Path) -> dict[str, float]:
    recall1: list[float] = []
    recall3: list[float] = []
    recall5: list[float] = []
    precision3: list[float] = []
    mrr: list[float] = []
    ndcg10: list[float] = []

    for case in cases:
        relevant_sources = set(case["relevant_sources"])
        results = retrieve_documents(case["query"], top_k=5, persist_dir=persist_dir)
        recall1.append(recall_at_k(results, relevant_sources, 1))
        recall3.append(recall_at_k(results, relevant_sources, 3))
        recall5.append(recall_at_k(results, relevant_sources, 5))
        precision3.append(precision_at_k(results, relevant_sources, 3))
        mrr.append(reciprocal_rank(results, relevant_sources))
        ndcg10.append(ndcg_at_k(results, case.get("graded_relevance", {}), 10))

    return {
        "recall_at_1": average_metric(recall1),
        "recall_at_3": average_metric(recall3),
        "recall_at_5": average_metric(recall5),
        "precision_at_3": average_metric(precision3),
        "mrr": average_metric(mrr),
        "ndcg_at_10": average_metric(ndcg10),
    }


def test_retrieval_quality_benchmark_compares_hybrid_and_sparse_fallback(benchmark_corpus: dict[str, object], benchmark_embedder, monkeypatch, tmp_path: Path) -> None:
    import rag.retriever as retriever_module

    cases = benchmark_corpus["retrieval_cases"]
    data_dir = Path(benchmark_corpus["data_dir"])

    monkeypatch.setattr(retriever_module, "get_embedder", lambda: benchmark_embedder)
    hybrid_dir = tmp_path / "hybrid_store"
    clear_vector_store_cache()
    initialize_retriever(data_dir=str(data_dir), persist_dir=hybrid_dir, rebuild=True)
    hybrid_metrics = _collect_metrics(cases, hybrid_dir)

    monkeypatch.setattr(retriever_module, "get_embedder", lambda: None)
    sparse_dir = tmp_path / "sparse_store"
    clear_vector_store_cache()
    initialize_retriever(data_dir=str(data_dir), persist_dir=sparse_dir, rebuild=True)
    sparse_metrics = _collect_metrics(cases, sparse_dir)

    payload = {
        "test_file": "tests/test_retrieval_quality.py",
        "queries": len(cases),
        "documents": len(benchmark_corpus["documents"]),
        "hybrid": hybrid_metrics,
        "fallback_sparse": sparse_metrics,
    }
    write_artifact("retrieval_metrics.json", payload)

    assert payload["queries"] >= 30
    assert hybrid_metrics["recall_at_1"] >= 0.65
    assert hybrid_metrics["recall_at_3"] >= 0.85
    assert hybrid_metrics["mrr"] >= 0.70
    assert sparse_metrics["recall_at_3"] >= 0.60
    assert hybrid_metrics["mrr"] >= sparse_metrics["mrr"]
    assert hybrid_metrics["ndcg_at_10"] >= sparse_metrics["ndcg_at_10"]
