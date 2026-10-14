from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, MutableMapping, Sequence

from agent.json_utils import parse_json_object


# These are deliberately public: callers can use the same limits when batching
# search requests and when validating an artifact produced by this module.
CANDIDATE_CAP_PER_SUBTOPIC = 30
GLOBAL_SELECTED_CAP = 60
CANDIDATE_CAP = CANDIDATE_CAP_PER_SUBTOPIC
HARD_GLOBAL_SELECTED_CAP = GLOBAL_SELECTED_CAP
MAX_CANDIDATES_PER_SUBTOPIC = CANDIDATE_CAP_PER_SUBTOPIC
MAX_SELECTED_CANDIDATES = GLOBAL_SELECTED_CAP
MAX_SELECTED_CANDIDATES_GLOBAL = GLOBAL_SELECTED_CAP

_FULLTEXT_KEYS = {
    "content",
    "full_text",
    "fulltext",
    "text",
    "body",
    "pages",
    "chunks",
    "full_text_docs",
    "retrieved_docs",
}
_METADATA_KEYS = {
    "id",
    "candidate_id",
    "arxiv_id",
    "doi",
    "title",
    "abstract",
    "summary",
    "authors",
    "published",
    "updated",
    "categories",
    "url",
    "entry_url",
    "pdf_url",
    "source",
    "source_type",
    "query",
    "query_label",
    "subtopic",
    "keywords",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ENGLISH_QUERY_RE = re.compile(r"[A-Za-z]")
_NON_ENGLISH_QUERY_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "this", "to", "using", "with", "without", "via", "we", "what", "how",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        item_text = _text(item)
        if item_text and item_text.lower() not in seen:
            seen.add(item_text.lower())
            result.append(item_text)
    return result


def _canonical_url(value: Any) -> str:
    url = _text(value).lower()
    return re.sub(r"^https?://(?:www\.)?", "", url).rstrip("/")


def _canonical_doi(value: Any) -> str:
    doi = _text(value).lower()
    doi = re.sub(r"^(?:https?://)?(?:doi\.org/)?", "", doi)
    return doi.rstrip("/ ")


def _candidate_keys(candidate: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for field_name in ("candidate_id", "id", "arxiv_id"):
        value = _text(candidate.get(field_name))
        if value:
            keys.append(f"id:{value.lower()}")
    doi = _canonical_doi(candidate.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
    for field_name in ("entry_url", "url", "pdf_url"):
        url = _canonical_url(candidate.get(field_name))
        if url:
            keys.append(f"url:{url}")
    title = _text(candidate.get("title")).lower()
    if title:
        keys.append(f"title:{title}")
    return keys


def _merge_metadata(original: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Fill missing metadata from a duplicate without replacing first-seen values."""
    merged = dict(original)
    for key, value in incoming.items():
        if key not in _METADATA_KEYS or key in _FULLTEXT_KEYS:
            continue
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
        elif key in {"authors", "categories", "keywords"}:
            merged[key] = _strings(list(merged.get(key, [])) + _strings(value))
    return merged


def normalize_candidate(candidate: Mapping[str, Any], *, subtopic: str = "", query: str = "") -> dict[str, Any]:
    """Return one stable, metadata-only candidate record.

    Search adapters often use slightly different names for the same fields.
    The output retains useful metadata but intentionally drops page text,
    chunks, and other full-text payloads.
    """
    source = dict(candidate)
    # Common adapter spellings are folded before the metadata allow-list is
    # applied.  This keeps the public output schema small and predictable.
    if not source.get("candidate_id") and source.get("paper_id"):
        source["candidate_id"] = source["paper_id"]
    if not source.get("subtopic"):
        source["subtopic"] = source.get("sub_topic") or source.get("topic") or subtopic
    if not source.get("abstract") and source.get("description"):
        source["abstract"] = source["description"]
    if not source.get("categories") and source.get("category"):
        source["categories"] = source["category"]
    normalized: dict[str, Any] = {}
    for key in _METADATA_KEYS:
        if key in source and key not in _FULLTEXT_KEYS:
            normalized[key] = source[key]

    for key in ("title", "abstract", "summary", "published", "updated", "url", "entry_url", "pdf_url", "source", "query", "query_label", "subtopic", "id", "candidate_id", "arxiv_id", "doi"):
        if key in normalized:
            normalized[key] = _text(normalized[key])
    for key in ("authors", "categories", "keywords"):
        if key in normalized:
            normalized[key] = _strings(normalized[key])

    if subtopic and not normalized.get("subtopic"):
        normalized["subtopic"] = _text(subtopic)
    if query and not normalized.get("query"):
        normalized["query"] = _text(query)

    identity = _text(normalized.get("candidate_id") or normalized.get("id") or normalized.get("arxiv_id"))
    if not identity:
        identity = _canonical_doi(normalized.get("doi")) or _canonical_url(
            normalized.get("entry_url") or normalized.get("url") or normalized.get("pdf_url")
        )
    if not identity:
        identity = re.sub(r"\s+", " ", _text(normalized.get("title"))).lower()
    if identity:
        normalized["candidate_id"] = identity
    normalized["metadata_only"] = True
    return normalized


def normalize_and_dedupe_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_per_subtopic: int = CANDIDATE_CAP_PER_SUBTOPIC,
    query: str = "",
) -> list[dict[str, Any]]:
    """Normalize and stable-dedupe candidates, capping each subtopic.

    The first occurrence controls ordering.  Later duplicate records only fill
    missing metadata, which makes repeated search results safe to merge.
    """
    limit = max(0, int(max_per_subtopic))
    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    subtopic_counts: dict[str, int] = {}
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        item = normalize_candidate(raw, query=query)
        keys = _candidate_keys(item)
        if not keys:
            continue
        existing_index = next((positions[key] for key in keys if key in positions), None)
        if existing_index is not None:
            result[existing_index] = _merge_metadata(result[existing_index], item)
            for key in keys:
                positions[key] = existing_index
            continue
        topic = _text(item.get("subtopic")).lower()
        if subtopic_counts.get(topic, 0) >= limit:
            continue
        index = len(result)
        result.append(item)
        subtopic_counts[topic] = subtopic_counts.get(topic, 0) + 1
        for key in keys:
            positions[key] = index
    return result


# Short aliases are useful for adapters that use "deduplicate" rather than
# "dedupe" in their naming.
dedupe_normalize_candidates = normalize_and_dedupe_candidates
normalize_candidates = normalize_and_dedupe_candidates
dedupe_candidates = normalize_and_dedupe_candidates
normalize_metadata_candidate = normalize_candidate


def extract_keywords(
    candidate_or_title: Mapping[str, Any] | str,
    abstract: str = "",
    categories: Sequence[str] | str = (),
    query: str = "",
    *,
    limit: int = 12,
) -> list[str]:
    """Extract deterministic weighted keywords from metadata and a query.

    Titles and categories receive more weight than abstracts and queries.  A
    frequency tie is resolved lexicographically, so results do not depend on
    hash iteration order or an external NLP package.
    """
    if isinstance(candidate_or_title, Mapping):
        candidate = candidate_or_title
        title = _text(candidate.get("title"))
        abstract_text = _text(candidate.get("abstract") or candidate.get("summary"))
        category_values = _strings(candidate.get("categories"))
        query_text = query
    else:
        title = _text(candidate_or_title)
        abstract_text = _text(abstract)
        category_values = _strings(categories)
        query_text = query

    weights = ((title, 4), (" ".join(category_values), 3), (abstract_text, 2), (_text(query_text), 1))
    scores: dict[str, int] = {}
    for text, weight in weights:
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOPWORDS or (len(token) < 2 and not _CJK_RE.fullmatch(token)):
                continue
            scores[token] = scores.get(token, 0) + weight
    return [token for token, _ in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[: max(0, int(limit))]]


def _artifact_records(candidates: Sequence[Mapping[str, Any]], query: str = "") -> list[dict[str, Any]]:
    records = normalize_and_dedupe_candidates(candidates, query=query)
    for record in records:
        record["keywords"] = extract_keywords(record, query=query)
    return records


def _safe_artifact_payload(artifact: Mapping[str, Any] | Sequence[Mapping[str, Any]], *, query: str = "") -> dict[str, Any]:
    if isinstance(artifact, Mapping) and "candidates" in artifact:
        resolved_query = _text(artifact.get("query")) or query
        raw_candidates = artifact.get("candidates", [])
        if not isinstance(raw_candidates, (list, tuple)):
            raw_candidates = []
        records = _artifact_records(raw_candidates, query=resolved_query)
        return {
            "schema_version": _text(artifact.get("schema_version")) or "deep-research-candidates.v1",
            "query": resolved_query,
            "candidate_cap_per_subtopic": CANDIDATE_CAP_PER_SUBTOPIC,
            "subtopics": sorted({_text(item.get("subtopic")) for item in records if _text(item.get("subtopic"))}),
            "candidate_count": len(records),
            "candidates": records,
        }
    return build_candidate_artifact(artifact, query=query)  # type: ignore[arg-type]


def build_candidate_artifact(candidates: Sequence[Mapping[str, Any]], *, query: str = "") -> dict[str, Any]:
    """Build a JSON-serializable metadata artifact without full-text content."""
    records = _artifact_records(candidates, query=query)
    subtopics = sorted({_text(item.get("subtopic")) for item in records if _text(item.get("subtopic"))})
    return {
        "schema_version": "deep-research-candidates.v1",
        "query": _text(query),
        "candidate_cap_per_subtopic": CANDIDATE_CAP_PER_SUBTOPIC,
        "subtopics": subtopics,
        "candidate_count": len(records),
        "candidates": records,
    }


@dataclass
class CandidateArtifact:
    """Convenience wrapper around :func:`build_candidate_artifact`."""

    candidates: Sequence[Mapping[str, Any]]
    query: str = ""
    _payload: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._payload = build_candidate_artifact(self.candidates, query=self.query)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._payload, ensure_ascii=False))

    def to_json(self) -> str:
        return candidate_artifact_to_json(self._payload)

    def to_markdown(self) -> str:
        return candidate_artifact_to_markdown(self._payload)


def candidate_artifact_to_json(artifact: Mapping[str, Any] | Sequence[Mapping[str, Any]], *, query: str = "") -> str:
    payload = _safe_artifact_payload(artifact, query=query)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def candidate_artifact_to_markdown(artifact: Mapping[str, Any] | Sequence[Mapping[str, Any]], *, query: str = "") -> str:
    payload = _safe_artifact_payload(artifact, query=query)
    lines = ["# Deep Research Candidate Artifact", "", f"- Query: {_text(payload.get('query')) or '(none)'}", f"- Candidates: {payload.get('candidate_count', len(payload.get('candidates', [])))}", ""]
    for index, candidate in enumerate(payload.get("candidates", []), 1):
        title = _text(candidate.get("title")) or _text(candidate.get("candidate_id")) or "Untitled candidate"
        lines.extend([f"## {index}. {title}", "", f"- Candidate ID: {_text(candidate.get('candidate_id')) or '(none)'}"])
        if candidate.get("subtopic"):
            lines.append(f"- Subtopic: {_text(candidate['subtopic'])}")
        if candidate.get("published"):
            lines.append(f"- Published: {_text(candidate['published'])}")
        authors = _strings(candidate.get("authors"))
        if authors:
            lines.append(f"- Authors: {', '.join(authors)}")
        if candidate.get("categories"):
            lines.append(f"- Categories: {', '.join(_strings(candidate['categories']))}")
        url = _text(candidate.get("entry_url") or candidate.get("url") or candidate.get("pdf_url"))
        if url:
            lines.append(f"- URL: {url}")
        if candidate.get("keywords"):
            lines.append(f"- Keywords: {', '.join(_strings(candidate['keywords']))}")
        abstract = _text(candidate.get("abstract") or candidate.get("summary"))
        if abstract:
            lines.extend(["", abstract])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def serialize_candidate_artifact(
    artifact: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    format: str = "json",
    *,
    query: str = "",
) -> str:
    """Serialize an artifact as deterministic JSON or readable Markdown."""
    normalized_format = _text(format).lower().lstrip(".")
    if normalized_format in {"md", "markdown"}:
        return candidate_artifact_to_markdown(artifact, query=query)
    if normalized_format == "json":
        return candidate_artifact_to_json(artifact, query=query)
    raise ValueError("format must be 'json' or 'markdown'")


serialize_candidate_artifact_json = candidate_artifact_to_json
serialize_candidate_artifact_markdown = candidate_artifact_to_markdown


def _bounded_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _unique_texts(values: Any, *, english_only: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values] if values is not None else []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        if english_only and (not _ENGLISH_QUERY_RE.search(text) or _NON_ENGLISH_QUERY_RE.search(text)):
            continue
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _first_value(data: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _text(value).lower()
    if text in {"true", "yes", "y", "1", "pass", "passed", "approved"}:
        return True
    if text in {"false", "no", "n", "0", "fail", "failed", "rejected"}:
        return False
    return default


def _parse_response_object(raw_response: Any) -> dict[str, Any]:
    if isinstance(raw_response, Mapping):
        return dict(raw_response)
    return parse_json_object(_text(raw_response))


def parse_selector_response(
    raw_response: str | Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Parse selector JSON and enforce the hard global selection cap.

    ``selected_count`` is an LLM decision: this function never substitutes a
    fixed default count.  It only clamps that decision to 60 and to the number
    of usable selected candidates supplied in the response.
    """
    data = _parse_response_object(raw_response)
    # Selector input is normally an already-capped candidate artifact.  Do not
    # cap an ungrouped caller-provided list a second time here: selection has a
    # separate, global hard limit and must be able to choose up to 60 records.
    candidate_records = normalize_and_dedupe_candidates(candidates, max_per_subtopic=max(CANDIDATE_CAP_PER_SUBTOPIC, len(candidates)))
    by_key: dict[str, dict[str, Any]] = {}
    for candidate in candidate_records:
        for key in _candidate_keys(candidate):
            by_key[key] = candidate

    entries = _first_value(data, ("selected_candidates", "selected", "selected_ids", "selected_candidate_ids", "selected_paper_ids", "selected_papers", "paper_ids"), [])
    if not isinstance(entries, list):
        entries = []
    selected_ids: list[str] = []
    selected_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, Mapping):
            identity = _text(_first_value(entry, ("candidate_id", "id", "arxiv_id", "doi", "entry_url", "url", "title"), ""))
        else:
            identity = _text(entry)
        if not identity:
            continue
        lookup = _candidate_keys({"candidate_id": identity, "title": identity, "doi": identity, "url": identity})
        record = next((by_key[key] for key in lookup if key in by_key), None)
        canonical = _text(record.get("candidate_id")) if record else identity
        if canonical.casefold() in seen:
            continue
        seen.add(canonical.casefold())
        selected_ids.append(canonical)
        if record:
            selected_records.append(dict(record))

    requested_value = _first_value(data, ("selected_count", "selection_count", "count"), len(selected_ids))
    requested_count = max(0, _bounded_int(requested_value, len(selected_ids)))
    selected_count = min(GLOBAL_SELECTED_CAP, requested_count, len(selected_ids))
    selected_ids = selected_ids[:selected_count]
    selected_records = selected_records[:selected_count]
    gaps = _unique_texts(_first_value(data, ("evidence_gaps", "evidence_gaps_found", "missing_evidence", "gaps"), []))
    queries = _unique_texts(
        _first_value(data, ("additional_english_queries", "additional_english_search_queries", "additional_queries", "additional_queries_en", "next_queries", "follow_up_queries"), []),
        english_only=True,
    )
    return {
        "selected_count": selected_count,
        "requested_selected_count": requested_count,
        "selected_ids": selected_ids,
        "selected_candidates": selected_records,
        "evidence_gaps": gaps,
        "additional_english_queries": queries,
        "rationale": _text(_first_value(data, ("rationale", "reason", "selection_reason"), "")),
    }


parse_selector_llm_response = parse_selector_response
parse_selector_json = parse_selector_response


def parse_report_review_response(raw_response: str | Mapping[str, Any]) -> dict[str, Any]:
    """Parse a report-review JSON response in isolation from the report context.

    A malformed/empty review is treated as insufficient, which is safer for a
    caller deciding whether to run another evidence round.
    """
    data = _parse_response_object(raw_response)
    nested_review = data.get("review")
    if isinstance(nested_review, Mapping):
        data = dict(nested_review)
    sufficient_value = _first_value(data, ("sufficient", "is_sufficient", "approved", "pass", "passed", "quality_ok", "verdict"), None)
    if sufficient_value is None:
        needs_revision = _first_value(data, ("needs_revision", "revise", "revision_needed"), True)
        sufficient = not _as_bool(needs_revision, default=True)
    else:
        sufficient = _as_bool(sufficient_value)
    gaps = _unique_texts(_first_value(data, ("evidence_gaps", "missing_evidence", "missing_points", "unsupported_claims", "gaps", "issues"), []))
    queries = _unique_texts(
        _first_value(data, ("additional_english_queries", "additional_english_search_queries", "additional_queries", "additional_queries_en", "english_queries", "next_queries", "follow_up_queries"), []),
        english_only=True,
    )
    return {
        "sufficient": sufficient,
        "evidence_gaps": gaps,
        "additional_english_queries": queries,
        "issues": _unique_texts(_first_value(data, ("issues", "problems"), [])),
        "reason": _text(_first_value(data, ("reason", "rationale", "summary"), "")),
    }


parse_fresh_context_report_review = parse_report_review_response
parse_report_review_json = parse_report_review_response
parse_report_review = parse_report_review_response
extract_candidate_keywords = extract_keywords
serialize_candidates_json = candidate_artifact_to_json
serialize_candidates_markdown = candidate_artifact_to_markdown


@dataclass
class StageTimer:
    """Collect wall-clock durations for named pipeline stages."""

    timings: MutableMapping[str, dict[str, Any]] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        record: dict[str, Any] = {"stage": _text(name), "started": started}
        try:
            yield record
        finally:
            finished = time.perf_counter()
            record["finished"] = finished
            record["duration_seconds"] = max(0.0, finished - started)
            record["duration_ms"] = record["duration_seconds"] * 1000.0
            self.timings[_text(name)] = record


@contextmanager
def stage_timing(name: str, timings: MutableMapping[str, dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
    """Record one stage in a caller-owned mapping."""
    timer = StageTimer(timings if timings is not None else {})
    with timer.stage(name) as record:
        yield record


timed_stage = stage_timing
StageTiming = StageTimer


__all__ = [
    "CANDIDATE_CAP_PER_SUBTOPIC",
    "CANDIDATE_CAP",
    "GLOBAL_SELECTED_CAP",
    "HARD_GLOBAL_SELECTED_CAP",
    "MAX_CANDIDATES_PER_SUBTOPIC",
    "MAX_SELECTED_CANDIDATES",
    "MAX_SELECTED_CANDIDATES_GLOBAL",
    "CandidateArtifact",
    "StageTimer",
    "StageTiming",
    "build_candidate_artifact",
    "candidate_artifact_to_json",
    "candidate_artifact_to_markdown",
    "dedupe_normalize_candidates",
    "dedupe_candidates",
    "extract_candidate_keywords",
    "extract_keywords",
    "normalize_and_dedupe_candidates",
    "normalize_candidate",
    "normalize_metadata_candidate",
    "parse_fresh_context_report_review",
    "parse_report_review_json",
    "parse_report_review_response",
    "parse_selector_llm_response",
    "parse_selector_json",
    "parse_selector_response",
    "parse_report_review",
    "serialize_candidate_artifact_json",
    "serialize_candidate_artifact_markdown",
    "serialize_candidate_artifact",
    "serialize_candidates_json",
    "serialize_candidates_markdown",
    "stage_timing",
    "timed_stage",
]
