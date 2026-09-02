"""稀疏检索（TF-IDF）与向量相似度的共享实现。

从 rag/retriever.py 抽出来，让 SimpleVectorStore 与 ChromaVectorStore 共用同一套
稀疏侧逻辑，避免两个后端的混合权重语义不一致，也避免循环 import。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from rag.text_tokenizer import tokenize

# 稠密/稀疏混合权重：与改动前 rag/retriever.py 的 0.7 / 0.3 保持一致
DENSE_WEIGHT = 0.7
SPARSE_WEIGHT = 0.3


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if not vector_a or not vector_b:
        return 0.0
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def sparse_cosine_similarity(vector_a: dict[str, float], vector_b: dict[str, float]) -> float:
    if not vector_a or not vector_b:
        return 0.0
    common_keys = set(vector_a) & set(vector_b)
    dot = sum(vector_a[key] * vector_b[key] for key in common_keys)
    norm_a = math.sqrt(sum(value * value for value in vector_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vector_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_sparse_index(documents: list[dict[str, Any]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    tokenized_docs = [tokenize(doc.get("page_content", "")) for doc in documents]
    doc_freq: Counter[str] = Counter()
    for tokens in tokenized_docs:
        doc_freq.update(set(tokens))

    total_docs = max(len(documents), 1)
    idf = {term: math.log((1 + total_docs) / (1 + freq)) + 1.0 for term, freq in doc_freq.items()}

    sparse_vectors: list[dict[str, float]] = []
    for tokens in tokenized_docs:
        tf = Counter(tokens)
        length = max(len(tokens), 1)
        sparse_vectors.append({term: (count / length) * idf.get(term, 1.0) for term, count in tf.items()})
    return sparse_vectors, idf


def build_query_sparse_vector(query: str, idf: dict[str, float]) -> dict[str, float]:
    tokens = tokenize(query)
    tf = Counter(tokens)
    length = max(len(tokens), 1)
    return {term: (count / length) * idf.get(term, 1.0) for term, count in tf.items()}
