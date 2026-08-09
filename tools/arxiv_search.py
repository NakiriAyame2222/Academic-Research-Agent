from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

ARXIV_API_URL = "http://export.arxiv.org/api/query"
USER_AGENT = "academic-research-agent/1.0"
ProgressCallback = Callable[[str], None]


@dataclass
class ArxivPaper:
    title: str
    summary: str
    authors: list[str]
    published: str
    arxiv_id: str
    pdf_url: str
    entry_url: str
    query: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "authors": self.authors,
            "published": self.published,
            "arxiv_id": self.arxiv_id,
            "pdf_url": self.pdf_url,
            "entry_url": self.entry_url,
            "query": self.query,
        }



def _parse_arxiv_feed(xml_text: str, query: str = "") -> list[ArxivPaper]:
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    root = ElementTree.fromstring(xml_text)
    papers: list[ArxivPaper] = []
    for entry in root.findall("atom:entry", namespace):
        title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", default="", namespaces=namespace) or "").strip().replace("\n", " ")
        authors = [author.findtext("atom:name", default="", namespaces=namespace).strip() for author in entry.findall("atom:author", namespace)]
        entry_url = entry.findtext("atom:id", default="", namespaces=namespace).strip()
        published = entry.findtext("atom:published", default="", namespaces=namespace).strip()
        pdf_url = ""
        for link in entry.findall("atom:link", namespace):
            if link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href", "")
                break
        arxiv_id = entry_url.rsplit("/", 1)[-1] if entry_url else title
        papers.append(
            ArxivPaper(
                title=title,
                summary=summary,
                authors=[author for author in authors if author],
                published=published,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                entry_url=entry_url,
                query=query,
            )
        )
    return papers



def search_arxiv(query: str, max_results: int = 5, progress_callback: ProgressCallback | None = None) -> list[dict]:
    normalized_query = " ".join(str(query).split())
    if normalized_query.lower().startswith("all:"):
        normalized_query = normalized_query[4:].strip()
    search_query = quote_plus(normalized_query)
    url = f"{ARXIV_API_URL}?search_query=all:{search_query}&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending"
    response = None
    if progress_callback:
        progress_callback(f"开始 arXiv 搜索：{normalized_query}")
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT}, allow_redirects=False)
        except requests.RequestException as exc:
            if progress_callback:
                progress_callback(f"arXiv 搜索异常，第 {attempt + 1}/3 次重试：{exc}")
            response = None
            time.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code == 429:
            if progress_callback:
                progress_callback(f"arXiv 返回 429，第 {attempt + 1}/3 次重试")
            response = None
            time.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            if progress_callback and location:
                progress_callback(f"arXiv 重定向到 {location}")
            if location:
                try:
                    response = requests.get(location, timeout=20, headers={"User-Agent": USER_AGENT})
                except requests.RequestException as exc:
                    if progress_callback:
                        progress_callback(f"跟随重定向失败：{exc}")
                    response = None
            if response is not None and response.status_code == 429:
                if progress_callback:
                    progress_callback(f"重定向后 arXiv 返回 429，第 {attempt + 1}/3 次重试")
                response = None
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        break
    if response is None:
        if progress_callback:
            progress_callback(f"arXiv 搜索失败，返回 0 篇：{normalized_query}")
        return []
    if response.status_code >= 400:
        if progress_callback:
            progress_callback(f"arXiv 返回 HTTP {response.status_code}，跳过该查询")
        return []
    xml_text = (response.text or "").strip()
    if not xml_text:
        if progress_callback:
            progress_callback(f"arXiv 返回空结果：{query}")
        return []
    try:
        papers = [paper.to_dict() for paper in _parse_arxiv_feed(xml_text, query=query)]
        if progress_callback:
            progress_callback(f"arXiv 搜索完成：{query}，命中 {len(papers)} 篇")
        return papers
    except ElementTree.ParseError:
        if progress_callback:
            progress_callback(f"arXiv 返回内容无法解析，跳过查询：{query}")
        return []



def search_surveys(topic: str, max_results: int = 5, progress_callback: ProgressCallback | None = None) -> list[dict]:
    queries = [
        f"{topic} survey",
        f"{topic} review",
        f"{topic} overview",
        f"{topic} tutorial",
    ]
    seen: set[str] = set()
    results: list[dict] = []
    for query in queries:
        for paper in search_arxiv(query, max_results=max_results, progress_callback=progress_callback):
            if paper["arxiv_id"] in seen:
                continue
            seen.add(paper["arxiv_id"])
            results.append(paper)
    if progress_callback:
        progress_callback(f"综述检索完成，共保留 {len(results)} 篇候选综述")
    return results



def expand_topic_queries(topic: str) -> list[str]:
    return [
        topic,
        f"{topic} methods",
        f"{topic} benchmark",
        f"{topic} enterprise applications",
    ]



def _safe_name(value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:10]
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value)[:40].strip("_")
    return f"{cleaned or 'paper'}_{digest}.pdf"



def download_arxiv_pdf(paper: dict, downloads_dir: str | Path, progress_callback: ProgressCallback | None = None) -> str | None:
    pdf_url = str(paper.get("pdf_url", "") or "").strip()
    if not pdf_url:
        entry_url = str(paper.get("entry_url", "") or "").strip()
        if entry_url:
            pdf_url = entry_url.replace("/abs/", "/pdf/") + ".pdf"
    if not pdf_url:
        if progress_callback:
            progress_callback(f"跳过无 PDF 链接论文：{paper.get('title', 'unknown')}")
        return None

    target_dir = Path(downloads_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / _safe_name(str(paper.get("arxiv_id") or paper.get("title") or "paper"))
    if target_path.exists() and target_path.stat().st_size > 0:
        if progress_callback:
            progress_callback(f"复用已下载全文：{paper.get('title', 'unknown')}")
        return str(target_path)

    if progress_callback:
        progress_callback(f"下载全文：{paper.get('title', 'unknown')}")
    response = requests.get(pdf_url, timeout=60, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    target_path.write_bytes(response.content)
    if progress_callback:
        progress_callback(f"全文下载完成：{paper.get('title', 'unknown')}")
    return str(target_path)
