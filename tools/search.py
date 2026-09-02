from __future__ import annotations

from typing import Callable

from config import get_settings
from rag.retriever import get_file_scope_dir, get_session_scope_dir, initialize_retriever, retrieve_documents
from tools.arxiv_search import download_arxiv_pdf, expand_topic_queries, search_arxiv, search_surveys

ProgressCallback = Callable[[str], None]



def search_papers(
    query: str,
    top_k: int = 5,
    file_path: str | None = None,
    rebuild: bool = False,
    persist_dir: str | None = None,
) -> list[dict]:
    settings = get_settings()
    if persist_dir:
        return retrieve_documents(query, top_k=top_k, persist_dir=persist_dir)

    if file_path:
        persist_dir = get_file_scope_dir(settings["vector_store_files_dir"], file_path) # type: ignore
        initialize_retriever(
            data_dir=None,
            persist_dir=persist_dir,
            chunk_size=settings["chunk_size"],
            chunk_overlap=settings["chunk_overlap"],
            rebuild=rebuild,
            file_path=file_path,
        )
        return retrieve_documents(query, top_k=top_k, persist_dir=persist_dir)

    initialize_retriever(
        data_dir=str(settings["papers_dir"]),
        persist_dir=settings["vector_store_corpus_dir"],
        chunk_size=settings["chunk_size"],
        chunk_overlap=settings["chunk_overlap"],
        rebuild=rebuild,
    )
    return retrieve_documents(query, top_k=top_k, persist_dir=settings["vector_store_corpus_dir"])



def search_academic_topic(
    topic: str,
    max_results: int = 5,
    query_batches: list[dict] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict]]:
    if progress_callback:
        progress_callback(f"开始检索主题：{topic}")
    surveys = search_surveys(topic, max_results=max_results, progress_callback=progress_callback)
    batch_items = query_batches or [{"label": "default", "query": query} for query in expand_topic_queries(topic)]
    papers: list[dict] = []
    search_runs: list[dict] = []
    for index, batch in enumerate(batch_items, 1):
        query = batch.get("query", topic)
        label = batch.get("label", query)
        if progress_callback:
            progress_callback(f"执行第 {index}/{len(batch_items)} 轮搜索：{label} -> {query}")
        results = search_arxiv(query, max_results=max_results, progress_callback=progress_callback)
        for paper in results:
            paper.setdefault("query", query)
            paper["query_label"] = label
        papers.extend(results)
        search_runs.append({"label": label, "query": query, "results": results})
        if progress_callback:
            progress_callback(f"第 {index} 轮完成，新增 {len(results)} 篇候选论文")
    if progress_callback:
        progress_callback(f"主题检索结束，共得到 {len(papers)} 篇候选论文")
    return {
        "queries": [batch.get("query", topic) for batch in batch_items],
        "surveys": surveys,
        "papers": papers,
        "search_runs": search_runs,
    }



def build_selected_papers_index(
    session_id: str,
    papers: list[dict],
    rebuild: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> tuple[str, list[dict]]:
    settings = get_settings()
    downloads_dir = settings["downloads_dir"] / session_id
    downloaded: list[dict] = []
    for index, paper in enumerate(papers, 1):
        if progress_callback:
            progress_callback(f"准备第 {index}/{len(papers)} 篇代表论文全文：{paper.get('title', 'unknown')}")
        try:
            file_path = download_arxiv_pdf(paper, downloads_dir, progress_callback=progress_callback)
        except Exception as exc:
            if progress_callback:
                progress_callback(f"全文下载失败，跳过：{paper.get('title', 'unknown')}（{exc}）")
            continue
        if not file_path:
            continue
        downloaded.append({**paper, "file_path": file_path})
    persist_dir = get_session_scope_dir(settings["vector_store_dir"], session_id, "selected_papers")
    file_paths = [item["file_path"] for item in downloaded]
    # 下载后的文件名是 _safe_name(arxiv_id) 生成的哈希名，不含标题。
    # 这里把 arxiv_id / 标题写进 chunk metadata，让下游能把证据块归属到具体论文。
    extra_metadata = {
        item["file_path"]: {
            "arxiv_id": str(item.get("arxiv_id", "") or ""),
            "paper_title": str(item.get("title", "") or ""),
            "entry_url": str(item.get("entry_url", "") or ""),
        }
        for item in downloaded
    }
    if progress_callback:
        progress_callback(f"开始为 {len(file_paths)} 篇全文建立索引")
    initialize_retriever(
        data_dir=None,
        persist_dir=persist_dir,
        chunk_size=settings["chunk_size"],
        chunk_overlap=settings["chunk_overlap"],
        rebuild=rebuild,
        file_paths=file_paths,
        extra_metadata=extra_metadata,
    )
    if progress_callback:
        progress_callback("全文索引构建完成")
    return str(persist_dir), downloaded
