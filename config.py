from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "data" / "llm_config.env"


def normalize_openai_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if normalized.endswith("/v1"):
        return normalized
    return normalized + "/v1"


class RuleBasedLLM:
    """Fallback model that clearly signals demo mode instead of fabricating grounded answers."""

    def invoke(self, prompt: str) -> str:
        return (
            "当前处于降级模式：未成功连接真实 LLM，因此我不能基于论文内容生成可靠解析。\n"
            "请提供有效的 model、api_key、base_url，或使用 --config-file 加载配置后再试。"
        )


class SimpleLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class OpenAICompatibleLLM:
    def __init__(self, model: str, api_key: str, base_url: str = "") -> None:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        normalized_base_url = normalize_openai_base_url(base_url)
        if normalized_base_url:
            client_kwargs["base_url"] = normalized_base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model

    def invoke(self, prompt: str) -> SimpleLLMResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content if response.choices else ""
        return SimpleLLMResponse(content or "")


class OpenAICompatibleEmbedder:
    def __init__(self, model: str, api_key: str, base_url: str = "") -> None:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        normalized_base_url = normalize_openai_base_url(base_url)
        if normalized_base_url:
            client_kwargs["base_url"] = normalized_base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in response.data]

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

    try:
        return OpenAICompatibleLLM(
            model=settings["llm_model"],
            api_key=api_key,
            base_url=settings["llm_base_url"],
        )
    except Exception:
        return RuleBasedLLM()


def get_embedder() -> OpenAICompatibleEmbedder | None:
    settings = get_settings()
    api_key = settings["embedding_api_key"]
    if not api_key:
        return None
    try:
        return OpenAICompatibleEmbedder(
            model=settings["embedding_model"],
            api_key=api_key,
            base_url=settings["embedding_base_url"],
        )
    except Exception:
        return None
