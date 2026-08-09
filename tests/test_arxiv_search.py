from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.benchmark_helpers import write_artifact
from tools.arxiv_search import _parse_arxiv_feed


def test_parse_arxiv_feed_extracts_core_fields() -> None:
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Test Paper One</title>
        <summary>Summary one.</summary>
        <id>http://arxiv.org/abs/2501.00001</id>
        <published>2025-01-01T00:00:00Z</published>
        <author><name>Alice</name></author>
        <author><name>Bob</name></author>
        <link title="pdf" href="http://arxiv.org/pdf/2501.00001.pdf" />
      </entry>
    </feed>
    """.strip()

    papers = _parse_arxiv_feed(xml, query="agent memory")

    assert len(papers) == 1
    assert papers[0].title == "Test Paper One"
    assert papers[0].authors == ["Alice", "Bob"]
    assert papers[0].arxiv_id == "2501.00001"
    assert papers[0].pdf_url.endswith("2501.00001.pdf")
    assert papers[0].query == "agent memory"


def test_search_surveys_deduplicates_across_multiple_queries(monkeypatch) -> None:
    from tools import arxiv_search as arxiv_module

    def fake_search_arxiv(query: str, max_results: int = 5, progress_callback=None) -> list[dict]:
        common = {
            "title": "Common Survey",
            "summary": "duplicate result",
            "authors": ["A"],
            "published": "2025-01-01",
            "arxiv_id": "2501.00001",
            "pdf_url": "http://arxiv.org/pdf/2501.00001.pdf",
            "entry_url": "http://arxiv.org/abs/2501.00001",
            "query": query,
        }
        unique = {
            "title": f"Unique for {query}",
            "summary": "unique result",
            "authors": ["B"],
            "published": "2025-02-01",
            "arxiv_id": f"{abs(hash(query)) % 100000:05d}",
            "pdf_url": "http://arxiv.org/pdf/2501.00002.pdf",
            "entry_url": "http://arxiv.org/abs/2501.00002",
            "query": query,
        }
        return [common, unique]

    monkeypatch.setattr(arxiv_module, "search_arxiv", fake_search_arxiv)

    results = arxiv_module.search_surveys("llm agent memory", max_results=3)

    arxiv_ids = [item["arxiv_id"] for item in results]
    assert arxiv_ids.count("2501.00001") == 1
    assert len(results) == 5


def test_download_arxiv_pdf_uses_entry_url_and_cache(monkeypatch, tmp_path: Path) -> None:
    from tools import arxiv_search as arxiv_module

    requested_urls: list[str] = []

    class Response:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, timeout: int, headers: dict[str, str]):
        requested_urls.append(url)
        return Response(b"%PDF-1.4\nmock pdf\n")

    monkeypatch.setattr(arxiv_module.requests, "get", fake_get)

    paper = {
        "title": "Cacheable Paper",
        "summary": "summary",
        "authors": ["A"],
        "published": "2025-01-01",
        "arxiv_id": "2501.99999",
        "pdf_url": "",
        "entry_url": "http://arxiv.org/abs/2501.99999",
    }

    first_path = arxiv_module.download_arxiv_pdf(paper, tmp_path)
    second_path = arxiv_module.download_arxiv_pdf(paper, tmp_path)

    assert first_path == second_path
    assert Path(first_path).exists()
    assert requested_urls == ["http://arxiv.org/pdf/2501.99999.pdf"]


def test_search_arxiv_offline_metrics_and_progress(monkeypatch) -> None:
    from tools import arxiv_search as arxiv_module

    progress_messages: list[str] = []

    class Response:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Agent Memory Benchmark</title>
        <summary>Benchmarking agent memory systems.</summary>
        <id>http://arxiv.org/abs/2502.00001</id>
        <published>2025-02-02T00:00:00Z</published>
        <author><name>Alice</name></author>
        <link title="pdf" href="http://arxiv.org/pdf/2502.00001.pdf" />
      </entry>
      <entry>
        <title>Grounded RAG Evaluation</title>
        <summary>Grounding and citation metrics.</summary>
        <id>http://arxiv.org/abs/2502.00002</id>
        <published>2025-02-03T00:00:00Z</published>
        <author><name>Bob</name></author>
        <link title="pdf" href="http://arxiv.org/pdf/2502.00002.pdf" />
      </entry>
    </feed>
    """.strip()

    monkeypatch.setattr(
        arxiv_module.requests,
        "get",
        lambda *args, **kwargs: Response(200, text=xml),
    )

    papers = arxiv_module.search_arxiv(
        "agent memory benchmark",
        max_results=5,
        progress_callback=progress_messages.append,
    )

    payload = {
        "query": "agent memory benchmark",
        "papers_returned": len(papers),
        "has_pdf_urls": all(bool(paper.get("pdf_url")) for paper in papers),
        "progress_events": len(progress_messages),
        "titles": [paper["title"] for paper in papers],
    }
    write_artifact("arxiv_search_metrics.json", payload)

    assert len(papers) == 2
    assert all(paper.get("arxiv_id") for paper in papers)
    assert payload["has_pdf_urls"]
    assert payload["progress_events"] >= 2


@pytest.mark.live
def test_search_arxiv_live_smoke() -> None:
    if os.environ.get("RESEARCH_AGENT_ENABLE_LIVE_TESTS") != "1":
        pytest.skip("set RESEARCH_AGENT_ENABLE_LIVE_TESTS=1 to enable live arXiv smoke test")

    from tools.arxiv_search import search_arxiv

    papers = search_arxiv("llm agent memory benchmark", max_results=3)

    assert papers
    assert papers[0]["title"]
    assert papers[0]["arxiv_id"]
