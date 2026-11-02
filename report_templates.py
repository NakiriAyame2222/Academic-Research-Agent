"""按任务类型选取写作模板；模板只控制输出，不参与工具或任务调度。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_TEMPLATES = {
    "paper_qa": {
        "name": "论文问答",
        "description": "直接回答问题，区分论文结论与证据不足之处。",
        "guidelines": ["先回答当前问题", "列出支持结论的原文证据", "说明局限与未解决问题"],
    },
    "topic_research": {
        "name": "文献综述",
        "description": "按研究问题组织多篇论文的比较，并保留引用来源。",
        "guidelines": ["交代研究背景", "比较代表方法与实验条件", "总结证据缺口与后续方向"],
    },
}


def get_report_template(intent_mode: str) -> dict[str, Any]:
    return deepcopy(_TEMPLATES.get(intent_mode, _TEMPLATES["paper_qa"]))
