from __future__ import annotations

import re
from typing import Any

from rag.text_tokenizer import term_overlap_score


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


def _recency_score(published: str) -> float:
    """把出版年份归一化到 [0,1]，作为新近度加权项。抽不到年份记 0。"""
    match = re.search(r"(\d{4})", str(published or ""))
    if not match:
        return 0.0
    year = int(match.group(1))
    return max(0.0, min(1.0, (year - 2015) / 12.0))


def select_top_papers(topic: str, papers: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """代表论文重排。

    早期是"主题词按 topic.split() 切分后的词面重叠"——中文主题没空格会退化成整串
    匹配，选出来的"代表论文"未必真代表。现在改为：embedding 语义相似度 ×0.6 +
    CJK 感知的词项重叠 ×0.25 + 新近度 ×0.15；embedder 不可用（或向量化失败 / 返回
    数量对不上）时整体退化为词项重叠 ×0.75 + 新近度 ×0.25。
    """
    deduped = dedupe_papers(papers)
    if not deduped:
        return []

    texts = [f"{paper.get('title', '')} {paper.get('summary', '')}".strip() for paper in deduped]

    # 主题 vs 标题+摘要的 embedding 余弦，一次批量算；不可用或数量对不上就整体降级为纯稀疏
    topic_vector = None
    doc_vectors = None
    from config import get_embedder

    embedder = get_embedder()
    if embedder is not None:
        try:
            candidate_vectors = embedder.embed_documents(texts)
            query_vector = embedder.embed_query(topic)
        except Exception:
            candidate_vectors, query_vector = None, None
        if candidate_vectors and query_vector and len(candidate_vectors) == len(texts):
            doc_vectors = candidate_vectors
            topic_vector = query_vector

    use_semantic = doc_vectors is not None and topic_vector is not None
    if use_semantic:
        from rag.sparse import cosine_similarity

        semantic_weight, overlap_weight, recency_weight = 0.6, 0.25, 0.15
    else:
        semantic_weight, overlap_weight, recency_weight = 0.0, 0.75, 0.25

    def score(indexed_paper: tuple[int, dict[str, Any]]) -> tuple[float, str]:
        index, paper = indexed_paper
        overlap = term_overlap_score(topic, texts[index])
        recency = _recency_score(paper.get("published", ""))
        semantic = 0.0
        if use_semantic:
            semantic = max(0.0, cosine_similarity(topic_vector, doc_vectors[index]))
        total = semantic_weight * semantic + overlap_weight * overlap + recency_weight * recency
        # 总分相同时用 published 兜底偏好更新的工作
        return total, str(paper.get("published", ""))

    ranked = sorted(enumerate(deduped), key=score, reverse=True)
    return [paper for _, paper in ranked[:limit]]


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
        "tool_trace": [],
        "reflections": [],
        "evidence_gaps": [],
        "report_outline": [],
        "survey_sections": [],
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


def add_tool_trace(working_document: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    """把本轮 tool calling 的调用轨迹追加进调研档案。

    evidence_agent 每轮取证结束调一次，传入的是**本轮** trace；补充取证会多轮，
    因此按轮次累积（extend）而不是替换，否则后一轮会覆盖前一轮的轨迹。
    """
    updated = dict(working_document)
    existing = list(updated.get("tool_trace", []))
    existing.extend(trace or [])
    updated["tool_trace"] = existing
    return updated


def add_report_outline(working_document: dict[str, Any], outline: list[str]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["report_outline"] = outline
    return updated


def add_reflection(working_document: dict[str, Any], reflection: dict[str, Any]) -> dict[str, Any]:
    """追加一条反思记录，并把最新的证据缺口 / 待回答问题提升到档案顶层。

    reflect 节点每轮产出一条 reflection（含 round / sufficient / evidence_gaps 等），
    多轮补充取证会累积多条 → append 到 reflections 列表；同时把 evidence_gaps /
    open_questions 同步到 working_document 顶层，让档案顶层直接反映"当前还缺什么"。
    """
    updated = dict(working_document)
    reflections = list(updated.get("reflections", []))
    reflections.append(reflection)
    updated["reflections"] = reflections
    updated["evidence_gaps"] = list(reflection.get("evidence_gaps", []))
    updated["open_questions"] = list(reflection.get("open_questions", []))
    return updated


def add_survey_sections(working_document: dict[str, Any], sections: list[dict[str, str]]) -> dict[str, Any]:
    """记录逐节撰写的综述章节（writer 只跑一次，直接替换整份章节列表）。"""
    updated = dict(working_document)
    updated["survey_sections"] = list(sections)
    return updated


def attach_final_survey(working_document: dict[str, Any], survey_artifact: dict[str, Any]) -> dict[str, Any]:
    updated = dict(working_document)
    updated["final_survey"] = survey_artifact
    updated["survey_text"] = survey_artifact.get("full_text", "")
    return updated
