from __future__ import annotations

from typing import Any


def _format_history(messages: list[dict[str, str]], max_turns: int = 8) -> str:
    recent_messages = messages[-max_turns:]
    return "\n".join(f"{item['role']}: {item['content']}" for item in recent_messages)


def _format_memories(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "无长期相关记忆。"
    return "\n".join(f"- [{item.get('memory_type', 'memory')}] {item.get('content', '')}" for item in memories)


def _format_context_layers(initial_question: str, middle_summary: str, recent_messages: list[dict[str, str]]) -> str:
    return (
        f"初始用户问题：{initial_question or '无'}\n\n"
        f"中间对话压缩摘要：\n{middle_summary or '无'}\n\n"
        f"最近对话：\n{_format_history(recent_messages)}"
    )


def get_planner_prompt(
    query: str,
    user_profile: dict[str, Any],
    skill: dict[str, Any],
    mode: str,
    initial_question: str = "",
    middle_summary: str = "",
    recent_messages: list[dict[str, str]] | None = None,
) -> str:
    return (
        "你是一个学术研究任务规划助手。\n"
        f"当前模式：{mode}\n"
        f"用户问题：{query}\n"
        f"用户偏好：{user_profile}\n"
        f"选中技能：{skill.get('name', 'unknown')}\n"
        f"技能步骤：{skill.get('steps', [])}\n\n"
        f"上下文如下：\n{_format_context_layers(initial_question, middle_summary, recent_messages or [])}\n\n"
        "请输出简洁执行规划，说明应该如何检索、分析和组织回答。"
    )


def get_query_rewrite_prompt(initial_question: str, middle_summary: str, recent_messages: list[dict[str, str]], current_question: str) -> str:
    return (
        "你是一个学术问答检索改写助手。\n"
        "请把当前问题改写成适合检索的独立查询，补全代词指代，但不要捏造事实。\n\n"
        f"{_format_context_layers(initial_question, middle_summary, recent_messages)}\n\n"
        f"当前问题：{current_question}\n"
        "只输出改写后的检索查询。"
    )


def get_search_plan_prompt(topic: str, user_profile: dict[str, Any], long_term_memories: list[dict[str, Any]]) -> str:
    return (
        "你是一个学术调研规划助手。\n"
        f"研究主题：{topic}\n"
        f"用户偏好：{user_profile}\n"
        f"相关长期记忆：{_format_memories(long_term_memories)}\n\n"
        "请输出一个 JSON 对象，包含："
        "goal（调研目标）、subtopics（子主题列表）、query_groups（搜索组名和说明）、open_questions（待回答问题列表）、"
        "selection_criteria（筛选论文标准列表）、report_outline（建议综述章节列表）。"
    )


def get_query_batch_prompt(topic: str, search_plan: dict[str, Any]) -> str:
    return (
        "你是一个 arXiv 检索 query 设计助手。\n"
        f"研究主题：{topic}\n"
        f"搜索计划：{search_plan}\n\n"
        "请输出一个 JSON 数组，每个元素包含 label 和 query 两个字段，"
        "覆盖 broad、survey、method、benchmark、application 等不同角度。"
    )


def get_research_prompt(
    query: str,
    docs_text: str,
    skill_steps: list[str],
    mode: str,
    initial_question: str,
    middle_summary: str,
    recent_messages: list[dict[str, str]],
    memory_summary: str,
    long_term_memories: list[dict[str, Any]],
) -> str:
    return (
        "你是一个学术研究分析助手，请基于检索资料生成结构化研究笔记。\n"
        f"模式：{mode}\n"
        f"当前问题：{query}\n"
        f"技能步骤：{skill_steps}\n"
        "请优先回答当前问题，并保留资料中的具体证据位置。\n\n"
        f"{_format_context_layers(initial_question, middle_summary, recent_messages)}\n\n"
        "短期记忆摘要：\n"
        f"{memory_summary or '无'}\n\n"
        "相关长期记忆：\n"
        f"{_format_memories(long_term_memories)}\n\n"
        "以下是检索资料：\n"
        f"{docs_text}\n\n"
        "请输出研究笔记，不要直接写最终答案。"
    )


def get_answer_prompt(
    query: str,
    notes: str,
    user_profile: dict[str, Any],
    mode: str,
    initial_question: str,
    middle_summary: str,
    recent_messages: list[dict[str, str]],
    active_document: dict[str, Any],
    survey_artifact: dict[str, Any] | None = None,
) -> str:
    language = user_profile.get("language", "Chinese")
    output_format = user_profile.get("format", "structured")
    tone = user_profile.get("tone", "academic")
    file_hint = active_document.get("file_path", "")
    survey_hint = ""
    if survey_artifact and survey_artifact.get("outline"):
        survey_hint = f"当前综述章节：{survey_artifact.get('outline', [])}\n"
    return (
        "你是一个学术研究问答助手，请根据研究笔记回答最新问题。\n"
        f"模式：{mode}\n"
        f"当前问题：{query}\n"
        f"输出语言：{language}\n"
        f"输出格式：{output_format}\n"
        f"写作风格：{tone}\n"
        f"当前锁定文件：{file_hint or '无'}\n"
        f"{survey_hint}"
        f"{_format_context_layers(initial_question, middle_summary, recent_messages)}\n\n"
        "以下是研究笔记：\n"
        f"{notes}\n\n"
        "请直接回答当前问题；如果涉及多篇论文，请区分每篇论文的结论；"
        "每个核心论断尽量保留并引用 [Sx]、page、chunk 位置；如果证据不足请明确说明；最后附上 Sources 小节。"
    )


def get_paper_note_prompt(topic: str, paper: dict[str, Any]) -> str:
    return (
        "你是一个学术论文笔记抽取助手。\n"
        f"研究主题：{topic}\n"
        f"论文标题：{paper.get('title', '')}\n"
        f"论文摘要：{paper.get('summary', '')}\n"
        "请用中文输出 JSON 对象，字段包括：problem, related_work, method, experiments, summary。"
    )


def get_fulltext_note_prompt(topic: str, paper: dict[str, Any], evidence_text: str) -> str:
    return (
        "你是一个学术论文全文研读助手。\n"
        f"研究主题：{topic}\n"
        f"论文标题：{paper.get('title', '')}\n"
        f"以下是论文全文检索出的关键证据：\n{evidence_text}\n\n"
        "请用中文输出 JSON 对象，字段包括：problem, method, experiments, strengths, limitations, summary。"
    )


def get_survey_outline_prompt(topic: str, working_document: dict[str, Any]) -> str:
    return (
        "你是一个学术综述大纲生成助手。\n"
        f"研究主题：{topic}\n"
        f"工作文档：{working_document}\n\n"
        "请输出 JSON 数组，每一项是一节综述标题，要求覆盖问题背景、代表方法、实验对比、研究空白与未来方向。"
    )


def get_survey_section_prompt(topic: str, section_title: str, evidence_text: str, working_document: dict[str, Any]) -> str:
    return (
        "你是一个学术综述写作助手。\n"
        f"研究主题：{topic}\n"
        f"当前章节：{section_title}\n"
        f"工作文档摘要：{working_document}\n"
        f"证据：\n{evidence_text}\n\n"
        "请撰写这一节的内容，要求引用证据并明确不同论文的贡献和差异。"
    )


def get_memory_selection_prompt(topic: str, notes: list[dict[str, Any]], final_answer: str = "", user_profile: dict[str, Any] | None = None) -> str:
    return (
        "你是一个长期记忆筛选助手。\n"
        f"当前研究主题：{topic}\n"
        f"候选研究笔记：{notes}\n"
        f"最终回答：{final_answer}\n"
        f"用户偏好：{user_profile or {}}\n\n"
        "请输出一个 JSON 数组，每个元素包含 memory_type 和 content 两个字段。"
        "每条内容都要简短、可长期复用，适合保存为用户偏好或研究摘要。最多输出 5 条。"
    )
