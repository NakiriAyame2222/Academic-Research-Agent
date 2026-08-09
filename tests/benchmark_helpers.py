from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)
DATA_DIR = Path(__file__).resolve().parent / "data"


def load_json_fixture(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def write_artifact(name: str, payload: dict[str, Any]) -> Path:
    path = ARTIFACTS_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def classification_report(rows: list[dict[str, str]], labels: list[str]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(1 for row in rows if row["expected"] == row["predicted"])
    confusion: dict[str, dict[str, int]] = {
        label: {other: 0 for other in labels} for label in labels
    }
    for row in rows:
        confusion[row["expected"]][row["predicted"]] += 1

    per_label: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(confusion[label].values()),
        }

    return {
        "samples": total,
        "accuracy": correct / total if total else 0.0,
        "confusion_matrix": confusion,
        "per_label": per_label,
        "errors": [row for row in rows if row["expected"] != row["predicted"]],
    }


def reciprocal_rank(results: list[dict[str, Any]], relevant_sources: set[str]) -> float:
    for index, item in enumerate(results, start=1):
        source_name = item.get("metadata", {}).get("source_name")
        if source_name in relevant_sources:
            return 1.0 / index
    return 0.0


def recall_at_k(results: list[dict[str, Any]], relevant_sources: set[str], k: int) -> float:
    if not relevant_sources:
        return 0.0
    hits = {
        item.get("metadata", {}).get("source_name")
        for item in results[:k]
        if item.get("metadata", {}).get("source_name") in relevant_sources
    }
    return len(hits) / len(relevant_sources)


def precision_at_k(results: list[dict[str, Any]], relevant_sources: set[str], k: int) -> float:
    window = results[:k]
    if not window:
        return 0.0
    hits = sum(1 for item in window if item.get("metadata", {}).get("source_name") in relevant_sources)
    return hits / len(window)


def ndcg_at_k(results: list[dict[str, Any]], relevance_by_source: dict[str, float], k: int) -> float:
    def dcg(scores: list[float]) -> float:
        total = 0.0
        for idx, score in enumerate(scores, start=1):
            total += score if idx == 1 else score / __import__("math").log2(idx + 1)
        return total

    observed = [relevance_by_source.get(item.get("metadata", {}).get("source_name"), 0.0) for item in results[:k]]
    ideal = sorted(relevance_by_source.values(), reverse=True)[:k]
    ideal_score = dcg(ideal)
    if ideal_score == 0:
        return 0.0
    return dcg(observed) / ideal_score


def average_metric(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def citation_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    exact = 0
    source_hits = 0
    page_hits = 0
    chunk_hits = 0
    excerpt_hits = 0
    for row in rows:
        source_ok = row["predicted"]["source_name"] == row["expected"]["source_name"]
        page_ok = row["predicted"].get("page") == row["expected"].get("page")
        chunk_ok = row["predicted"].get("chunk_index") == row["expected"].get("chunk_index")
        excerpt_ok = row["expected"]["excerpt_fragment"] in row["predicted"].get("excerpt", "")
        source_hits += int(source_ok)
        page_hits += int(page_ok)
        chunk_hits += int(chunk_ok)
        excerpt_hits += int(excerpt_ok)
        exact += int(source_ok and page_ok and chunk_ok and excerpt_ok)

    return {
        "samples": total,
        "citation_precision": source_hits / total if total else 0.0,
        "citation_recall": source_hits / total if total else 0.0,
        "page_accuracy": page_hits / total if total else 0.0,
        "chunk_accuracy": chunk_hits / total if total else 0.0,
        "excerpt_hit_rate": excerpt_hits / total if total else 0.0,
        "exact_match_rate": exact / total if total else 0.0,
        "errors": [row for row in rows if row["predicted"]["source_name"] != row["expected"]["source_name"]],
    }


def retention_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    initial_hits = sum(1 for row in rows if row["initial_goal_ok"])
    recent_hits = sum(1 for row in rows if row["recent_fact_ok"])
    middle_hits = sum(1 for row in rows if row["middle_fact_ok"])
    savings = [row["compression_ratio"] for row in rows]
    return {
        "samples": total,
        "initial_goal_retention": initial_hits / total if total else 0.0,
        "recent_retention": recent_hits / total if total else 0.0,
        "middle_retention": middle_hits / total if total else 0.0,
        "avg_token_saving_rate_proxy": average_metric(savings),
        "rows": rows,
    }


def aggregate_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter(str(row.get(key, "")) for row in rows)
    return dict(counter)


def group_rows(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, ""))].append(row)
    return dict(grouped)
