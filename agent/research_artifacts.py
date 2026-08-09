from __future__ import annotations

import json
from typing import Any


def parse_json_list(raw_text: str) -> list[str]:
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass

    lines = []
    for line in raw_text.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if stripped:
            lines.append(stripped)
    return lines


def dedupe_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for paper in papers:
        key = str(paper.get("arxiv_id") or paper.get("entry_url") or paper.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(paper)
    return deduped


def select_top_papers(topic: str, papers: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    topic_terms = {term.lower() for term in topic.split() if term.strip()}

    def score(paper: dict[str, Any]) -> tuple[int, str]:
        haystack = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
        overlap = sum(1 for term in topic_terms if term in haystack)
        published = str(paper.get("published", ""))
        return overlap, published

    ranked = sorted(dedupe_papers(papers), key=score, reverse=True)
    return ranked[:limit]


def create_working_document(topic: str, search_plan: dict[str, Any], query_batches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "topic": topic,
        "search_plan": search_plan,
        "query_batches": query_batches,
        "search_runs": [],
        "candidate_papers": [],
        "selected_papers": [],
        "paper_notes_brief": [],
        "paper_notes_fulltext": [],
        "report_outline": [],
        "survey_text": "",
        "open_questions": list(search_plan.get("open_questions", [])),
        "final_survey": {},
    }


def add_search_run(working_document: dict[str, Any], query: str, results: list[dict[str, Any]], label: str = "") -> dict[str, Any]:
    updated = dict(working_document)
    runs = list(updated.get("search_runs", []))
    runs.append(
        {
            "label": label or query,
            "query": query,
            "result_count": len(results),
            "paper_ids": [paper.get("arxiv_id", "") for paper in results],
        }
    )
    updated["search_runs"] = runs
    return updated


def add_candidate_paper_notes(working_document: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["paper_notes_brief"] = notes
    updated["candidate_papers"] = [dict(note) for note in notes]
    return updated


def mark_selected_papers(working_document: dict[str, Any], selected_papers: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["selected_papers"] = selected_papers
    selected_ids = {paper.get("arxiv_id") for paper in selected_papers}
    candidate_papers = []
    for paper in updated.get("candidate_papers", []):
        item = dict(paper)
        item["selection_status"] = "selected" if item.get("arxiv_id") in selected_ids else "candidate"
        candidate_papers.append(item)
    updated["candidate_papers"] = candidate_papers
    return updated


def add_fulltext_notes(working_document: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["paper_notes_fulltext"] = notes
    return updated


def add_report_outline(working_document: dict[str, Any], outline: list[str]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["report_outline"] = outline
    return updated


def attach_final_survey(working_document: dict[str, Any], survey_artifact: dict[str, Any]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["final_survey"] = survey_artifact
    updated["survey_text"] = survey_artifact.get("full_text", "")
    return updated


def build_working_document(
    topic: str,
    search_queries: list[str],
    surveys: list[dict[str, Any]],
    papers: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    query_batches = [{"label": "default", "query": query} for query in search_queries]
    working_document = create_working_document(topic, {"topic": topic, "open_questions": []}, query_batches)
    working_document["selected_surveys"] = surveys
    working_document["selected_papers"] = papers
    working_document["paper_notes_brief"] = notes
    working_document["candidate_papers"] = notes
    return working_document
