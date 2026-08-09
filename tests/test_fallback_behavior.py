from __future__ import annotations

from pathlib import Path

from config import RuleBasedLLM, get_llm
from rag.retriever import SimpleRetriever, build_vector_store


class FailingQueryEmbedder:
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
        raise RuntimeError("query embedding unavailable")



def test_get_llm_returns_rule_based_fallback_when_api_key_missing(monkeypatch) -> None:
    import config as config_module

    monkeypatch.setattr(config_module, "load_default_config_file", lambda: None)
    monkeypatch.delenv("RESEARCH_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCH_AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("RESEARCH_AGENT_BASE_URL", raising=False)

    llm = get_llm()

    assert isinstance(llm, RuleBasedLLM)
    message = llm.invoke("请总结这篇论文")
    assert "降级模式" in message
    assert "不能基于论文内容生成可靠解析" in message



def test_retriever_falls_back_to_sparse_when_query_embedding_fails(sample_paper_docs: dict[str, object], monkeypatch, tmp_path: Path) -> None:
    import rag.retriever as retriever_module

    data_dir = Path(sample_paper_docs["data_dir"])
    persist_dir = tmp_path / "failing_query_store"
    embedder = FailingQueryEmbedder(
        {
            "doc_attention": [1.0, 0.0, 0.0],
            "doc_memory": [0.0, 1.0, 0.0],
            "doc_distractor": [0.0, 0.0, 1.0],
        }
    )
    monkeypatch.setattr(retriever_module, "get_embedder", lambda: embedder)
    retriever_module.clear_vector_store_cache()
    retriever = build_vector_store(
        retriever_module.prepare_documents(data_dir=str(data_dir)),
        persist_dir=str(persist_dir),
    ).as_retriever({"k": 3})

    results = retriever.invoke("attention complexity")

    assert results
    assert results[0]["metadata"]["source_name"] == "attention_note.md"



def test_simple_retriever_returns_documents_when_all_scores_are_zero() -> None:
    retriever = SimpleRetriever(
        documents=[
            {"page_content": "A", "metadata": {"source_name": "a.md"}},
            {"page_content": "B", "metadata": {"source_name": "b.md"}},
        ],
        embeddings=[],
        sparse_vectors=[{}, {}],
        idf={},
        top_k=1,
    )

    results = retriever.invoke("unrelated query")

    assert len(results) == 1
    assert results[0]["metadata"]["source_name"] == "a.md"



def test_prepare_documents_raises_on_missing_file() -> None:
    from rag.loader import prepare_documents

    missing_path = "D:/does-not-exist/missing-paper.pdf"
    try:
        prepare_documents(file_path=missing_path)
    except FileNotFoundError as exc:
        assert missing_path in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError for missing paper")



def test_pdf_loader_falls_back_to_fitz_when_pypdf_returns_empty(monkeypatch, tmp_path: Path) -> None:
    from rag import loader as loader_module

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

    monkeypatch.setattr(loader_module, "_load_pdf_with_pypdf", lambda path: [])
    monkeypatch.setattr(
        loader_module,
        "_load_pdf_with_fitz",
        lambda path: [
            {
                "page_content": "fitz fallback text",
                "metadata": {"source": str(path), "source_name": path.name, "page": 1, "chunk_index": 0},
            }
        ],
    )

    docs = loader_module.load_documents(file_path=str(pdf_path))

    assert docs
    assert docs[0]["page_content"] == "fitz fallback text"



def test_search_arxiv_handles_retry_redirect_and_bad_xml(monkeypatch) -> None:
    from tools import arxiv_search as arxiv_module

    class Response:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    xml = """
    <feed xmlns=\"http://www.w3.org/2005/Atom\">
      <entry>
        <title>Test Paper</title>
        <summary>Summary</summary>
        <id>http://arxiv.org/abs/1234.5678</id>
        <published>2026-01-01T00:00:00Z</published>
        <author><name>Alice</name></author>
        <link title=\"pdf\" href=\"http://arxiv.org/pdf/1234.5678.pdf\" />
      </entry>
    </feed>
    """.strip()

    responses = iter(
        [
            Response(429),
            Response(302, headers={"Location": "https://redirected.example/arxiv"}),
            Response(200, text=xml),
        ]
    )

    def fake_get(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(arxiv_module.requests, "get", fake_get)
    monkeypatch.setattr(arxiv_module.time, "sleep", lambda *_args, **_kwargs: None)

    papers = arxiv_module.search_arxiv("test query", max_results=3)
    assert len(papers) == 1
    assert papers[0]["arxiv_id"] == "1234.5678"

    monkeypatch.setattr(arxiv_module.requests, "get", lambda *args, **kwargs: Response(200, text="<bad xml"))
    papers = arxiv_module.search_arxiv("bad xml", max_results=3)
    assert papers == []
