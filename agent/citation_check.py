"""引用事后校验。

改动前：报告里的 [Sx] 编号完全没有校验，模型写错编号或凭空造一个 [S9] 不会被发现，
而 state["citations"] 里其实已经有完整的引用元数据了。

现在：正则提取全部 [Sx] -> 与真实 citations 比对 -> 标注非法编号 -> 缺失
Sources 小节时用真实 citations 补齐 -> 审计结果写进 state["citation_audit"]。
"""
from __future__ import annotations

import re
from typing import Any

CITATION_PATTERN = re.compile(r"\[S(\d+)\]")
SOURCES_HEADING_WORDS = ("sources", "参考来源", "引用来源", "来源", "参考文献")
_HEADING_PREFIX_PATTERN = re.compile(r"^[\s#>*_`-]+")


def has_sources_section(text: str) -> bool:
    for line in (text or "").splitlines():
        stripped = _HEADING_PREFIX_PATTERN.sub("", line).strip().lower().rstrip(":：*_`")
        if stripped.startswith(SOURCES_HEADING_WORDS):
            return True
    return False


def extract_citation_ids(text: str) -> list[str]:
    return [f"S{match}" for match in CITATION_PATTERN.findall(text or "")]


def format_sources_section(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return ""
    lines = ["", "## Sources", ""]
    for citation in citations:
        locator = f"p.{citation['page']}" if citation.get("page") is not None else f"chunk {citation.get('chunk_index', 0)}"
        # 下载文件名是 _safe_name(arxiv_id) 生成的哈希名，有标题就优先显示标题
        label = citation.get("paper_title") or citation.get("source_name") or citation.get("source", "unknown")
        arxiv_hint = f" [arXiv:{citation['arxiv_id']}]" if citation.get("arxiv_id") else ""
        section = f" | {citation['section_title']}" if citation.get("section_title") else ""
        lines.append(f"- {citation.get('id', '')}: {label}{arxiv_hint} ({locator}){section}")
    return "\n".join(lines)


def audit_citations(text: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    used = extract_citation_ids(text)
    valid_ids = {str(citation.get("id", "")) for citation in citations}
    invalid = sorted({citation_id for citation_id in used if citation_id not in valid_ids}, key=lambda item: int(item[1:]))
    used_valid = {citation_id for citation_id in used if citation_id in valid_ids}
    return {
        "total_references": len(used),
        "distinct_references": len(set(used)),
        "available_citations": len(valid_ids),
        "invalid_references": invalid,
        "valid_reference_rate": (len(used) - sum(1 for item in used if item in invalid)) / len(used) if used else 0.0,
        "citation_coverage": len(used_valid) / len(valid_ids) if valid_ids else 0.0,
        "has_sources_section": has_sources_section(text),
    }


def annotate_invalid_references(text: str, valid_ids: set[str]) -> str:
    """把无法对应到检索证据的编号就地标注出来，而不是静默留在报告里。"""

    def _replace(match: re.Match[str]) -> str:
        citation_id = f"S{match.group(1)}"
        if citation_id in valid_ids:
            return match.group(0)
        return f"[{citation_id}?未匹配到检索证据]"

    return CITATION_PATTERN.sub(_replace, text or "")


def enforce_citations(text: str, citations: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """返回 (修订后的正文, 审计结果)。"""
    audit = audit_citations(text, citations)
    revised = text or ""

    if audit["invalid_references"]:
        revised = annotate_invalid_references(revised, {str(item.get("id", "")) for item in citations})
        revised += (
            "\n\n> 引用校验提示：以下编号未能对应到本轮检索证据，已就地标注 —— "
            + "、".join(audit["invalid_references"])
        )

    if citations and not audit["has_sources_section"]:
        revised = revised.rstrip() + "\n" + format_sources_section(citations) + "\n"
        audit["sources_section_appended"] = True

    return revised, audit
