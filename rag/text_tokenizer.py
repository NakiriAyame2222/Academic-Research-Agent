"""共享分词器。

改动前 rag/retriever.py 用 re.compile(r"\\w+") 分词：汉字属于 \\w，
所以"潜变量推理的研究"会被当成一整个词，中文场景下 TF-IDF 退化成"整句精确匹配"，
那 30% 的稀疏权重几乎白给。

现在：jieba 可用时中文走 jieba；不可用时对连续汉字串取字符 bigram，
ASCII 部分始终保持词级切分（保证英文语料的检索指标不受影响）。
"""
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
