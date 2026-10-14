from __future__ import annotations

import re

try:  # pragma: no cover - 可选依赖
    import jieba

    jieba.setLogLevel(60)
except ImportError:  # pragma: no cover
    jieba = None

# 分词实现变更会让已持久化的 sparse_vectors.json / idf.json 失效，
# 通过这个版本号在 load_vector_store 时强制重建。
TOKENIZER_VERSION = "cjk-v1-jieba" if jieba is not None else "cjk-v1-bigram"

_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
_CJK_RUN_PATTERN = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")
_CJK_CHAR_PATTERN = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]")

_STOP_TOKENS = {"的", "了", "和", "与", "是", "在", "有", "对", "把", "被", "或", "及", "个", "them", "the", "a", "an", "of"}


def has_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_PATTERN.search(text or ""))


def _cjk_tokens(run: str) -> list[str]:
    if jieba is not None:
        words = [word.strip() for word in jieba.cut_for_search(run) if word.strip()]
        tokens = [word for word in words if word not in _STOP_TOKENS]
        # 单字过多时补 bigram，避免 jieba 把长专有名词切碎后失去搭配信息
        if len(run) >= 2:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        return tokens
    if len(run) == 1:
        return [run]
    return [run[index : index + 2] for index in range(len(run) - 1)]


def tokenize(text: str) -> list[str]:
    if not text:
        return []

    tokens: list[str] = []
    for ascii_match in _ASCII_TOKEN_PATTERN.findall(text):
        tokens.append(ascii_match.lower())

    for cjk_run in _CJK_RUN_PATTERN.findall(text):
        tokens.extend(_cjk_tokens(cjk_run))

    return tokens


def term_overlap_score(query: str, document: str) -> float:
    """归一化的词项重叠，用于论文筛选等轻量打分场景。"""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    document_tokens = set(tokenize(document))
    if not document_tokens:
        return 0.0
    return len(query_tokens & document_tokens) / len(query_tokens)
