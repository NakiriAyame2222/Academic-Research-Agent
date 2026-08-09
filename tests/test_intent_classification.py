from __future__ import annotations

from pathlib import Path

from app import detect_intent_mode
from tests.benchmark_helpers import classification_report, load_json_fixture, write_artifact


LABELS = ["paper_qa", "topic_research"]


def test_detect_intent_mode_benchmark_reports_realistic_metrics(tmp_path: Path) -> None:
    cases = load_json_fixture("intent_cases.json")
    paper_path = tmp_path / "local_paper.md"
    paper_path.write_text("# local paper\n", encoding="utf-8")

    rows = []
    for case in cases:
        query = case["query"]
        file_path = None
        if case.get("file_path_hint"):
            file_path = str(paper_path)
        predicted = detect_intent_mode(query, file_path=file_path)
        rows.append({"query": query, "expected": case["expected"], "predicted": predicted})

    metrics = classification_report(rows, LABELS)
    metrics["test_file"] = "tests/test_intent_classification.py"
    write_artifact("intent_metrics.json", metrics)

    assert metrics["samples"] >= 50
    assert metrics["accuracy"] >= 0.90
    assert metrics["per_label"]["paper_qa"]["recall"] >= 0.85
    assert metrics["per_label"]["topic_research"]["recall"] >= 0.90
    assert metrics["confusion_matrix"]["paper_qa"]["topic_research"] <= 6


def test_detect_intent_mode_treats_existing_local_document_path_as_paper_qa(tmp_path: Path) -> None:
    paper_path = tmp_path / "sample.md"
    paper_path.write_text("# Sample Paper\n", encoding="utf-8")

    assert detect_intent_mode(str(paper_path)) == "paper_qa"
