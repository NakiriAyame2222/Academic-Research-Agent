from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "data" / "llm_config.env"

# 中转端点未必支持 tools / response_format。首次被拒后把参数名记进来，
# 后续请求自动脱掉该参数，让整条链降级到手工 JSON mode 而不是直接崩。
_UNSUPPORTED_PARAMS: set[str] = set()
_DEGRADE_NOTICES: list[str] = []
_LLM_CACHE: dict[tuple[str, str, str, str], Any] = {}
_EMBEDDER_CACHE: dict[tuple[str, str, str, str], Any] = {}

# 中转端点常常很慢（thinking 模型尤甚），默认给足超时并允许重试
DEFAULT_REQUEST_TIMEOUT = 180.0


def normalize_openai_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if normalized.endswith("/v1"):
        return normalized
    return normalized + "/v1"


def supports_param(param: str) -> bool:
    return param not in _UNSUPPORTED_PARAMS


def take_degrade_notices() -> list[str]:
    """取出并清空累积的降级提示，供 emit_progress 展示。"""
    notices = list(_DEGRADE_NOTICES)
    _DEGRADE_NOTICES.clear()
    return notices


def reset_llm_capability_cache() -> None:
    _UNSUPPORTED_PARAMS.clear()
    _DEGRADE_NOTICES.clear()


def _looks_like_unsupported(param: str, exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    if param.lower() not in text:
        return False
    return any(
        marker in text
        for marker in ("unsupport", "not support", "invalid", "unknown", "unrecognized", "does not", "no such", "400")
    )


class RuleBasedLLM:
    """Fallback model that clearly signals demo mode instead of fabricating grounded answers."""

    def invoke(self, prompt: str, system: str | None = None) -> str:
        return (
            "当前处于演示/降级模式：未成功连接真实 LLM，因此我不能基于论文内容生成可靠解析。\n"
            "请提供有效的 model、api_key、base_url，或使用 --config-file 加载配置后再试。"
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "content": self.invoke(""),
            "tool_calls": [],
            "degraded": True,
            "raw": None,
        }


class SimpleLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


def _build_http_client(proxy_setting: str):
    """按 RESEARCH_AGENT_PROXY 构造 httpx client。

    默认（未设置）返回 None，即沿用 openai SDK 的默认行为（读取环境/系统代理）。
    设为 none/off/0 时绕过系统代理 —— Windows 注册表里常见的
    `https=https://127.0.0.1:7890`（Clash 混合端口其实只监听 http）会让 httpx
    对本地代理发起 TLS 握手并直接失败，这个开关用于绕开这类错配。
    也可以直接写一个代理 URL 显式指定。
    """
    normalized = (proxy_setting or "").strip()
    if not normalized:
        return None
    try:
        import httpx
    except ImportError:  # pragma: no cover - openai 依赖 httpx，正常装不缺
        return None
    if normalized.lower() in {"none", "off", "0", "false", "direct"}:
        return httpx.Client(trust_env=False, timeout=DEFAULT_REQUEST_TIMEOUT)
    return httpx.Client(trust_env=False, proxy=normalized, timeout=DEFAULT_REQUEST_TIMEOUT)


def _client_kwargs(api_key: str, base_url: str, proxy_setting: str = "", timeout: float | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout or DEFAULT_REQUEST_TIMEOUT, "max_retries": 2}
    normalized_base_url = normalize_openai_base_url(base_url)
    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url
    http_client = _build_http_client(proxy_setting)
    if http_client is not None:
        kwargs["http_client"] = http_client
    return kwargs


class OpenAICompatibleLLM:
    def __init__(self, model: str, api_key: str, base_url: str = "", proxy: str = "", timeout: float | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(**_client_kwargs(api_key, base_url, proxy, timeout))
        self.model = model

    def _create(self, payload: dict[str, Any]) -> Any:
        """按需脱掉端点不支持的参数后调用，脱参结论会被缓存。"""
        for name in ("tools", "response_format"):
            if name in _UNSUPPORTED_PARAMS:
                payload.pop(name, None)
        if "tools" not in payload:
            payload.pop("tool_choice", None)
        try:
            return self.client.chat.completions.create(**payload)
        except Exception as exc:
            dropped = [name for name in ("tools", "response_format") if name in payload and _looks_like_unsupported(name, exc)]
            if not dropped:
                raise
            for name in dropped:
                _UNSUPPORTED_PARAMS.add(name)
                _DEGRADE_NOTICES.append(f"当前 LLM 端点不支持 {name}，已自动降级（后续请求不再携带该参数）")
                payload.pop(name, None)
                if name == "tools":
                    payload.pop("tool_choice", None)
            return self.client.chat.completions.create(**payload)

    def invoke(self, prompt: str, system: str | None = None) -> SimpleLLMResponse:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._create({"model": self.model, "messages": messages, "temperature": 0})
        content = response.choices[0].message.content if response.choices else ""
        return SimpleLLMResponse(content or "")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format

        response = self._create(payload)
        message = response.choices[0].message if response.choices else None
        tool_calls = []
        for call in (getattr(message, "tool_calls", None) or []):
            function = getattr(call, "function", None)
            tool_calls.append(
                {
                    "id": getattr(call, "id", "") or "",
                    "name": getattr(function, "name", "") or "",
                    "arguments": getattr(function, "arguments", "") or "",
                }
            )
        return {
            "content": (getattr(message, "content", "") or "") if message else "",
            "tool_calls": tool_calls,
            "degraded": bool(tools) and "tools" in _UNSUPPORTED_PARAMS,
            "raw": message,
        }



class OpenAICompatibleEmbedder:
    # 一次请求的最大文本条数。整份语料一次性提交会超出多数 embedding 端点的
    # 批量上限（3000+ 条会被拒或超时），必须分批。
    EMBED_BATCH_SIZE = 64

    def __init__(self, model: str, api_key: str, base_url: str = "", proxy: str = "", timeout: float | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(**_client_kwargs(api_key, base_url, proxy, timeout))
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.EMBED_BATCH_SIZE):
            batch = texts[start : start + self.EMBED_BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model, input=batch)
            vectors.extend(list(item.embedding) for item in response.data)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=[text])
        return list(response.data[0].embedding)


def load_kv_config_file(file_path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(file_path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def apply_config_values(values: dict[str, str]) -> None:
    mapping = {
        "LLM_MODEL": "RESEARCH_AGENT_LLM_MODEL",
        "API_KEY": "RESEARCH_AGENT_API_KEY",
        "BASE_URL": "RESEARCH_AGENT_BASE_URL",
        "EMBEDDING_MODEL": "RESEARCH_AGENT_EMBEDDING_MODEL",
        "Embedding_MODEL": "RESEARCH_AGENT_EMBEDDING_MODEL",
        "EMBEDDING_API_KEY": "RESEARCH_AGENT_EMBEDDING_API_KEY",
        "Embedding_API_KEY": "RESEARCH_AGENT_EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL": "RESEARCH_AGENT_EMBEDDING_BASE_URL",
        "Embedding_BASE_URL": "RESEARCH_AGENT_EMBEDDING_BASE_URL",
        "PROXY": "RESEARCH_AGENT_PROXY",
        "Proxy": "RESEARCH_AGENT_PROXY",
        "RESEARCH_AGENT_LLM_MODEL": "RESEARCH_AGENT_LLM_MODEL",
        "RESEARCH_AGENT_API_KEY": "RESEARCH_AGENT_API_KEY",
        "RESEARCH_AGENT_BASE_URL": "RESEARCH_AGENT_BASE_URL",
        "RESEARCH_AGENT_EMBEDDING_MODEL": "RESEARCH_AGENT_EMBEDDING_MODEL",
        "RESEARCH_AGENT_EMBEDDING_API_KEY": "RESEARCH_AGENT_EMBEDDING_API_KEY",
        "RESEARCH_AGENT_EMBEDDING_BASE_URL": "RESEARCH_AGENT_EMBEDDING_BASE_URL",
    }
    for key, target_key in mapping.items():
        if key in values and values[key]:
            os.environ[target_key] = values[key]


def persist_config_values(values: dict[str, str], file_path: str | None = None) -> Path:
    target = Path(file_path) if file_path else DEFAULT_CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "LLM_MODEL": values.get("LLM_MODEL") or values.get("RESEARCH_AGENT_LLM_MODEL") or "",
        "API_KEY": values.get("API_KEY") or values.get("RESEARCH_AGENT_API_KEY") or "",
        "BASE_URL": values.get("BASE_URL") or values.get("RESEARCH_AGENT_BASE_URL") or "",
        "EMBEDDING_MODEL": values.get("EMBEDDING_MODEL") or values.get("Embedding_MODEL") or values.get("RESEARCH_AGENT_EMBEDDING_MODEL") or "",
        "EMBEDDING_API_KEY": values.get("EMBEDDING_API_KEY") or values.get("Embedding_API_KEY") or values.get("RESEARCH_AGENT_EMBEDDING_API_KEY") or "",
        "EMBEDDING_BASE_URL": values.get("EMBEDDING_BASE_URL") or values.get("Embedding_BASE_URL") or values.get("RESEARCH_AGENT_EMBEDDING_BASE_URL") or "",
    }
    content = "\n".join(f"{key}={value}" for key, value in normalized.items() if value)
    target.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return target


def load_default_config_file() -> None:
    if not DEFAULT_CONFIG_FILE.exists():
        return
    values = load_kv_config_file(str(DEFAULT_CONFIG_FILE))
    mapping = {
        "LLM_MODEL": "RESEARCH_AGENT_LLM_MODEL",
        "API_KEY": "RESEARCH_AGENT_API_KEY",
        "BASE_URL": "RESEARCH_AGENT_BASE_URL",
        "EMBEDDING_MODEL": "RESEARCH_AGENT_EMBEDDING_MODEL",
        "Embedding_MODEL": "RESEARCH_AGENT_EMBEDDING_MODEL",
        "EMBEDDING_API_KEY": "RESEARCH_AGENT_EMBEDDING_API_KEY",
        "Embedding_API_KEY": "RESEARCH_AGENT_EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL": "RESEARCH_AGENT_EMBEDDING_BASE_URL",
        "Embedding_BASE_URL": "RESEARCH_AGENT_EMBEDDING_BASE_URL",
        "PROXY": "RESEARCH_AGENT_PROXY",
        "Proxy": "RESEARCH_AGENT_PROXY",
    }
    for source_key, target_key in mapping.items():
        if os.getenv(target_key):
            continue
        value = values.get(source_key) or values.get(target_key)
        if value:
            os.environ[target_key] = value


def get_settings() -> dict[str, Any]:
    load_default_config_file()
    data_dir = PROJECT_ROOT / "data"
    vector_root = data_dir / "vector_store"
    downloads_dir = data_dir / "downloads"
    return {
        "project_root": PROJECT_ROOT,
        "data_dir": data_dir,
        "papers_dir": data_dir / "papers",
        "downloads_dir": downloads_dir,
        "vector_store_dir": vector_root,
        "vector_store_corpus_dir": vector_root / "corpus",
        "vector_store_files_dir": vector_root / "files",
        "sqlite_db_path": data_dir / "memory.db",
        "skills_dir": PROJECT_ROOT / "skills",
        "reports_dir": data_dir / "reports",
        "default_top_k": 4,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "llm_model": os.getenv("RESEARCH_AGENT_LLM_MODEL", "gpt-4o-mini"),
        "llm_base_url": os.getenv("RESEARCH_AGENT_BASE_URL", ""),
        "llm_api_key": os.getenv("RESEARCH_AGENT_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "embedding_model": os.getenv("RESEARCH_AGENT_EMBEDDING_MODEL") or os.getenv("RESEARCH_AGENT_LLM_MODEL", "text-embedding-3-small"),
        "embedding_api_key": os.getenv("RESEARCH_AGENT_EMBEDDING_API_KEY") or os.getenv("RESEARCH_AGENT_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "embedding_base_url": os.getenv("RESEARCH_AGENT_EMBEDDING_BASE_URL") or os.getenv("RESEARCH_AGENT_BASE_URL", ""),
        "vector_backend": (os.getenv("RESEARCH_AGENT_VECTOR_BACKEND") or "auto").strip().lower(),
        "proxy": (os.getenv("RESEARCH_AGENT_PROXY") or "").strip(),
        "request_timeout": float(os.getenv("RESEARCH_AGENT_REQUEST_TIMEOUT") or DEFAULT_REQUEST_TIMEOUT),
        "llm_compression_enabled": (os.getenv("RESEARCH_AGENT_LLM_COMPRESSION") or "1").strip() not in {"0", "false", "no"},
        "llm_preferences_enabled": (os.getenv("RESEARCH_AGENT_LLM_PREFERENCES") or "1").strip() not in {"0", "false", "no"},
        "tool_calling_enabled": (os.getenv("RESEARCH_AGENT_TOOL_CALLING") or "1").strip() not in {"0", "false", "no"},
        "max_research_rounds": int(os.getenv("RESEARCH_AGENT_MAX_RESEARCH_ROUNDS") or 2),
        "default_config_file": DEFAULT_CONFIG_FILE,
    }


def ensure_directories(settings: dict[str, Any]) -> None:
    directories = [
        settings["data_dir"],
        settings["papers_dir"],
        settings["downloads_dir"],
        settings["vector_store_dir"],
        settings["vector_store_corpus_dir"],
        settings["vector_store_files_dir"],
        settings["reports_dir"],
        settings["skills_dir"],
    ]
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


def get_llm():
    settings = get_settings()
    api_key = settings["llm_api_key"]
    if not api_key:
        return RuleBasedLLM()

    cache_key = (settings["llm_model"], api_key, settings["llm_base_url"], settings["proxy"])
    cached = _LLM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        llm = OpenAICompatibleLLM(
            model=settings["llm_model"],
            api_key=api_key,
            base_url=settings["llm_base_url"],
            proxy=settings["proxy"],
            timeout=settings["request_timeout"],
        )
    except Exception:
        return RuleBasedLLM()
    _LLM_CACHE[cache_key] = llm
    return llm


def llm_is_available(llm: Any | None = None) -> bool:
    """判断当前是否有真实 LLM 可用（供压缩、偏好抽取等模块决定是否降级）。"""
    return not isinstance(llm if llm is not None else get_llm(), RuleBasedLLM)


def get_embedder() -> OpenAICompatibleEmbedder | None:
    settings = get_settings()
    api_key = settings["embedding_api_key"]
    if not api_key:
        return None

    cache_key = (settings["embedding_model"], api_key, settings["embedding_base_url"], settings["proxy"])
    cached = _EMBEDDER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        embedder = OpenAICompatibleEmbedder(
            model=settings["embedding_model"],
            api_key=api_key,
            base_url=settings["embedding_base_url"],
            proxy=settings["proxy"],
            timeout=settings["request_timeout"],
        )
    except Exception:
        return None
    _EMBEDDER_CACHE[cache_key] = embedder
    return embedder
