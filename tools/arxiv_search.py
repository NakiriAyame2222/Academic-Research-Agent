from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

ARXIV_API_URL = "https://export.arxiv.org/api/query"
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


def _atom_error_message(xml_text: str) -> str | None:
    """Return the message from an arXiv API error entry, if present."""

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    root = ElementTree.fromstring(xml_text)
    for entry in root.findall("atom:entry", namespace):
        entry_id = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip().lower()
        title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=namespace) or "").strip()
        if "/api/errors" in entry_id or entry_id.startswith("http://arxiv.org/api/errors") or entry_id.startswith("https://arxiv.org/api/errors"):
            return summary or title or "未知 Atom 错误"
    return None



def _sleep_or_cancel(seconds: float, cancel_event: Any | None = None) -> bool:
    if cancel_event and getattr(cancel_event, "wait", None):
        return bool(cancel_event.wait(timeout=seconds))
    time.sleep(seconds)
    return False


_RATE_LIMIT_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = 3.0  # arXiv recommends at least three seconds between requests
_MAX_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_MAX_RETRY_DELAY = 30.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _configured_request_interval() -> float:
    """Return the process-wide arXiv request interval.

    The environment override is read for each request so tests and long-running
    processes can tune it without importing a second configuration module.
    """

    raw_value = os.environ.get("ARXIV_MIN_REQUEST_INTERVAL")
    if raw_value is None:
        raw_value = os.environ.get("ARXIV_REQUEST_INTERVAL")
    if raw_value is not None:
        try:
            return max(0.0, float(raw_value))
        except (TypeError, ValueError):
            pass
    return max(0.0, float(_MIN_REQUEST_INTERVAL))


def _acquire_rate_limit(cancel_event: Any | None = None) -> None:
    global _LAST_REQUEST_TIME
    interval = _configured_request_interval()
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        if interval <= 0:
            _LAST_REQUEST_TIME = now
            return
        scheduled_time = max(now, _LAST_REQUEST_TIME)
        delay = scheduled_time - now
        # Reserve the next slot while holding the lock. This keeps concurrent
        # topic workers from issuing requests at the same time, without
        # holding the lock while sleeping.
        _LAST_REQUEST_TIME = scheduled_time + interval
    if delay and _sleep_or_cancel(delay, cancel_event):
        raise InterruptedError("Cancelled during rate limit wait")


def _header_value(headers: Any, name: str) -> str:
    if not headers or not hasattr(headers, "items"):
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value).strip()
    return ""


def _retry_after_seconds(headers: Any) -> float | None:
    value = _header_value(headers, "Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.astimezone()
        return max(0.0, retry_at.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _retry_delay(attempt: int, headers: Any = None) -> float:
    retry_after = _retry_after_seconds(headers)
    if retry_after is not None:
        return retry_after
    return min(_MAX_RETRY_DELAY, _RETRY_BASE_DELAY * (2**attempt))


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, TimeoutError, ConnectionError)):
        return True
    try:
        import httpx

        return isinstance(exc, httpx.RequestError)
    except ImportError:
        return False


async def _async_cancellable_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    cancel_event: Any | None = None,
    allow_redirects: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("Cancelled before request")

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    import httpx

    proxy = os.getenv("ARXIV_PROXY", "").strip()
    system_proxy = proxy.lower() in {"system", "env"}
    kwargs = {"trust_env": system_proxy}
    if proxy and proxy.lower() not in {"system", "env", "none", "off", "direct"}:
        kwargs["proxy"] = proxy
    client = httpx.AsyncClient(timeout=timeout, headers=req_headers, follow_redirects=allow_redirects, **kwargs)
    try:
        async def _execute():
            resp = await client.get(url)
            return resp.status_code, resp.content, dict(resp.headers)

        req_task = asyncio.create_task(_execute())

        while not req_task.done():
            if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
                req_task.cancel()
                try:
                    await req_task
                except (asyncio.CancelledError, BaseException):
                    pass
                raise InterruptedError("Cancelled while waiting for server")
            await asyncio.sleep(0.01)

        try:
            return await req_task
        except asyncio.CancelledError:
            raise InterruptedError("Cancelled while waiting for server")
    finally:
        await client.aclose()


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def _cancellable_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    cancel_event: Any | None = None,
    allow_redirects: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    # 限制在途请求而非仅限制开始间隔；不同会话也不能同时请求 arXiv。
    while not _REQUEST_LOCK.acquire(timeout=0.05):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled while queued for arXiv")
    try:
        return _serialized_get(url, headers, timeout, cancel_event, allow_redirects)
    finally:
        _REQUEST_LOCK.release()


def _serialized_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    cancel_event: Any | None = None,
    allow_redirects: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("Cancelled before request")

    _acquire_rate_limit(cancel_event)

    if cancel_event is not None:
        try:
            return _run_async(
                _async_cancellable_get(
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    cancel_event=cancel_event,
                    allow_redirects=allow_redirects,
                )
            )
        except InterruptedError:
            raise
        except Exception as exc:
            if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
                raise InterruptedError("Cancelled during request") from exc
            raise

    # 简单同步无令牌兼容路径
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    request_kwargs: dict[str, Any] = {"timeout": timeout, "headers": req_headers}
    proxy = os.getenv("ARXIV_PROXY", "").strip()
    if proxy.lower() not in {"system", "env"}:
        explicit = proxy if proxy and proxy.lower() not in {"none", "off", "direct"} else ""
        request_kwargs["proxies"] = {"http": explicit, "https": explicit, "all": explicit}
    if not allow_redirects:
        request_kwargs["allow_redirects"] = False
    resp = requests.get(url, **request_kwargs)
    status_code = getattr(resp, "status_code", 200)
    body = getattr(resp, "content", b"")
    if not body and hasattr(resp, "text"):
        body = (resp.text or "").encode("utf-8")
    resp_headers = dict(getattr(resp, "headers", {}))
    return status_code, body, resp_headers


def _request_with_retries(
    url: str,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> tuple[int, bytes, dict[str, str]] | None:
    """Fetch an arXiv endpoint with bounded retries for transient failures."""

    for attempt in range(_MAX_RETRY_ATTEMPTS):
        if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
            raise InterruptedError("Search cancelled")
        try:
            status_code, body, response_headers = _cancellable_get(
                url,
                timeout=20,
                cancel_event=cancel_event,
                allow_redirects=False,
            )
            if status_code in {301, 302, 303, 307, 308}:
                location = _header_value(response_headers, "Location")
                if location:
                    if progress_callback:
                        progress_callback(f"arXiv 重定向到 {location}")
                    status_code, body, response_headers = _cancellable_get(
                        location,
                        timeout=20,
                        cancel_event=cancel_event,
                        allow_redirects=True,
                    )

            if not _is_retryable_status(status_code):
                return status_code, body, response_headers

            if attempt == _MAX_RETRY_ATTEMPTS - 1:
                return status_code, body, response_headers
            delay = _retry_delay(attempt, response_headers)
            if progress_callback:
                progress_callback(
                    f"arXiv 返回 HTTP {status_code}，第 {attempt + 1}/{_MAX_RETRY_ATTEMPTS} 次重试，等待 {delay:g} 秒"
                )
            if _sleep_or_cancel(delay, cancel_event):
                raise InterruptedError("Search cancelled during backoff")
        except InterruptedError:
            raise
        except Exception as exc:
            if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
                raise InterruptedError("Search cancelled") from exc
            if not _is_retryable_exception(exc):
                if progress_callback:
                    progress_callback(f"arXiv 请求失败，跳过该查询：{exc}")
                return None
            if attempt == _MAX_RETRY_ATTEMPTS - 1:
                if progress_callback:
                    progress_callback(f"arXiv 请求失败，已达到 {_MAX_RETRY_ATTEMPTS} 次尝试：{exc}")
                return None
            delay = _retry_delay(attempt)
            if progress_callback:
                progress_callback(
                    f"arXiv 搜索异常，第 {attempt + 1}/{_MAX_RETRY_ATTEMPTS} 次重试，等待 {delay:g} 秒：{exc}"
                )
            if _sleep_or_cancel(delay, cancel_event):
                raise InterruptedError("Search cancelled during backoff")
    return None


def search_arxiv(
    query: str,
    max_results: int = 5,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> list[dict]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("Search cancelled")
    normalized_query = " ".join(str(query).split())
    if normalized_query.lower().startswith("all:"):
        normalized_query = normalized_query[4:].strip()
    search_query = quote_plus(normalized_query)
    url = f"{ARXIV_API_URL}?search_query=all:{search_query}&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending"

    if progress_callback:
        progress_callback(f"开始 arXiv 搜索：{normalized_query}")
    response = _request_with_retries(url, progress_callback=progress_callback, cancel_event=cancel_event)
    if response is None:
        if progress_callback:
            progress_callback(f"arXiv 搜索失败，返回 0 篇：{normalized_query}")
        return []

    status_code, raw_bytes, _ = response
    if status_code >= 400:
        if progress_callback:
            progress_callback(f"arXiv 返回 HTTP {status_code}，跳过该查询")
        return []
    xml_text = raw_bytes.decode("utf-8", errors="ignore").strip()
    if not xml_text:
        if progress_callback:
            progress_callback(f"arXiv 返回空结果：{query}")
        return []
    try:
        atom_error = _atom_error_message(xml_text)
        if atom_error:
            if progress_callback:
                progress_callback(f"arXiv 返回 Atom 错误条目，跳过查询：{atom_error}")
            return []
        papers = [paper.to_dict() for paper in _parse_arxiv_feed(xml_text, query=query)]
        if progress_callback:
            progress_callback(f"arXiv 搜索完成：{query}，命中 {len(papers)} 篇")
        return papers
    except ElementTree.ParseError:
        if progress_callback:
            progress_callback(f"arXiv 返回内容无法解析，跳过查询：{query}")
        return []


def search_surveys(
    topic: str,
    max_results: int = 5,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> list[dict]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("Search cancelled")
    queries = [
        f"{topic} survey",
        f"{topic} review",
        f"{topic} overview",
        f"{topic} tutorial",
    ]
    seen: set[str] = set()
    results: list[dict] = []
    for query in queries:
        if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
            raise InterruptedError("Search cancelled")
        kwargs = {"max_results": max_results, "progress_callback": progress_callback}
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        for paper in search_arxiv(query, **kwargs):
            if paper["arxiv_id"] in seen:
                continue
            seen.add(paper["arxiv_id"])
            results.append(paper)
    if progress_callback and not (cancel_event and getattr(cancel_event, "is_set", lambda: False)()):
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


def download_arxiv_pdf(
    paper: dict,
    downloads_dir: str | Path,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
) -> str | None:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        raise InterruptedError("Download cancelled")
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
    tmp_path = target_dir / f".{target_path.name}.tmp_{uuid.uuid4().hex[:8]}"
    try:
        if cancel_event is not None:
            status_code, body, _ = _cancellable_get(pdf_url, timeout=60, cancel_event=cancel_event, allow_redirects=True)
            if status_code >= 400:
                return None
            tmp_path.write_bytes(body)
        else:
            status_code, content, _ = _cancellable_get(pdf_url, timeout=60, cancel_event=None, allow_redirects=True)
            if status_code >= 400 or not content:
                return None
            tmp_path.write_bytes(content)
        os.replace(tmp_path, target_path)
    except InterruptedError:
        raise
    except Exception:
        return None
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    if progress_callback:
        progress_callback(f"全文下载完成：{paper.get('title', 'unknown')}")
    return str(target_path)
