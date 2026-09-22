"""运行时模型配置：Web 端的 --model/--api-key/--base-url 等价物。

单用户本地工具，无鉴权。写 data/llm_config.env 持久化（与 --save-config 同一文件），
并同步 env + 清空 LLM/embedder 缓存让新配置立即生效。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from config import (
    _EMBEDDER_CACHE,
    _LLM_CACHE,
    apply_config_values,
    get_settings,
    persist_config_values,
    reset_llm_capability_cache,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"***{secret[-4:]}" if len(secret) > 4 else "***"


class SettingsView(BaseModel):
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""  # 掩码
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""  # 掩码
    max_research_rounds: int = 2
    default_top_k: int = 4


class SettingsUpdate(BaseModel):
    llm_model: str = ""
    llm_base_url: str = ""
    # 空字符串 = 不修改；"__clear__" = 清除该项
    llm_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    max_research_rounds: int | None = None
    default_top_k: int | None = None


@router.get("", response_model=SettingsView)
def view_settings() -> SettingsView:
    settings = get_settings()
    return SettingsView(
        llm_model=str(settings.get("llm_model", "")),
        llm_base_url=str(settings.get("llm_base_url", "")),
        llm_api_key=_mask(str(settings.get("llm_api_key", ""))),
        embedding_model=str(settings.get("embedding_model", "")),
        embedding_base_url=str(settings.get("embedding_base_url", "")),
        embedding_api_key=_mask(str(settings.get("embedding_api_key", ""))),
        max_research_rounds=int(settings.get("max_research_rounds") or 2),
        default_top_k=int(settings.get("default_top_k") or 4),
    )


@router.post("", response_model=SettingsView)
def update_settings(update: SettingsUpdate) -> SettingsView:
    """更新模型配置：env 立即生效 + 持久化到 data/llm_config.env + 清缓存。"""
    current = get_settings()

    def resolve(submitted: str, env_key: str) -> str:
        if submitted == "__clear__":
            return ""
        if submitted:
            return submitted
        return str(current.get(env_key, ""))

    values = {
        "LLM_MODEL": resolve(update.llm_model, "llm_model"),
        "API_KEY": resolve(update.llm_api_key, "llm_api_key"),
        "BASE_URL": resolve(update.llm_base_url, "llm_base_url"),
        "EMBEDDING_MODEL": resolve(update.embedding_model, "embedding_model"),
        "EMBEDDING_API_KEY": resolve(update.embedding_api_key, "embedding_api_key"),
        "EMBEDDING_BASE_URL": resolve(update.embedding_base_url, "embedding_base_url"),
    }
    # 未提交时回落到当前值，避免重写 llm_config.env 时把已有运行参数抹掉
    if update.max_research_rounds is None:
        values["MAX_RESEARCH_ROUNDS"] = str(current.get("max_research_rounds") or 2)
    else:
        values["MAX_RESEARCH_ROUNDS"] = str(max(1, min(int(update.max_research_rounds), 5)))
    if update.default_top_k is None:
        values["DEFAULT_TOP_K"] = str(current.get("default_top_k") or 4)
    else:
        values["DEFAULT_TOP_K"] = str(max(1, min(int(update.default_top_k), 10)))

    # __clear__ 的项要从 env 里也清掉（apply_config_values 只写非空值）
    import os

    if update.llm_api_key == "__clear__":
        os.environ.pop("RESEARCH_AGENT_API_KEY", None)
    if update.embedding_api_key == "__clear__":
        os.environ.pop("RESEARCH_AGENT_EMBEDDING_API_KEY", None)

    apply_config_values(values)
    persist_config_values(values)
    _LLM_CACHE.clear()
    _EMBEDDER_CACHE.clear()
    reset_llm_capability_cache()
    return view_settings()
