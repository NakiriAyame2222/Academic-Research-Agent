from __future__ import annotations

from typing import Any

# 改动前所有角色设定都塞在唯一一条 user message 里，没有用 system role。
# 这里把角色设定抽成常量，由 agent/llm.py 放进真正的 system message。
PLANNER_SYSTEM = "你是一个学术研究任务规划助手，擅长把模糊的研究请求拆解成可执行的检索与分析计划。"
REWRITE_SYSTEM = "你是一个学术问答检索改写助手，只输出改写后的查询本身。"
SEARCH_PLAN_SYSTEM = "你是一个学术调研规划助手，只输出严格的 JSON 对象，不要附加解释文字。"
QUERY_BATCH_SYSTEM = "你是一个 arXiv 检索 query 设计助手，只输出严格的 JSON 数组。"
NOTE_SYSTEM = "你是一个学术论文笔记抽取助手，只输出严格的 JSON 对象。"
RESEARCH_SYSTEM = "你是一个学术研究分析助手，只依据给定证据写作，不要编造未出现的内容。"
ANSWER_SYSTEM = "你是一个学术研究问答助手，回答必须可溯源，每个核心论断都要带 [Sx] 证据编号。"
MEMORY_SYSTEM = "你是一个长期记忆筛选助手，只输出严格的 JSON 数组。"
REFLECT_SYSTEM = "你是一个研究证据审查员，负责判断现有证据能否支撑结论，只输出严格的 JSON 对象。"
SURVEY_OUTLINE_SYSTEM = "你是一个学术综述大纲生成助手，只输出严格的 JSON 数组。"
SURVEY_SECTION_SYSTEM = "你是一个学术综述写作助手，按给定章节撰写内容并保留证据编号。"
COMPRESSION_SYSTEM = "你是一个对话上下文压缩助手，负责在压缩的同时保住关键事实。"
PREFERENCE_SYSTEM = "你是一个用户偏好抽取助手，只输出严格的 JSON 对象。"

# 每个 prompt 首行都带唯一任务标记，便于日志定位与测试分派
TASK_PLANNER = "任务标记：planner_plan"
TASK_QUERY_REWRITE = "任务标记：query_rewrite"
TASK_SEARCH_PLAN = "任务标记：search_plan"
TASK_QUERY_BATCH = "任务标记：query_batches"
TASK_TOPIC_AGENT = "任务标记：topic_agent_goal"
TASK_PAPER_QA_AGENT = "任务标记：paper_qa_agent_goal"
TASK_PAPER_NOTE = "任务标记：paper_note_brief"
TASK_FULLTEXT_NOTE = "任务标记：paper_note_fulltext"
TASK_RESEARCH_NOTES = "任务标记：research_notes"
TASK_REFLECTION = "任务标记：evidence_reflection"
TASK_SURVEY_OUTLINE = "任务标记：survey_outline"
TASK_SURVEY_SECTION = "任务标记：survey_section"
TASK_FINAL_ANSWER = "任务标记：final_answer"
TASK_MEMORY_SELECTION = "任务标记：memory_selection"
TASK_MIDDLE_SUMMARY = "任务标记：context_compression"
TASK_PREFERENCES = "任务标记：preference_extraction"

TOPIC_AGENT_SYSTEM = (
    "你是一个学术调研 agent，可以自主调用工具来收集证据。\n"
    "工作原则：\n"
    "1. arXiv 检索必须使用英文关键词；中文主题要先自己翻译成英文术语。\n"
    "2. 先用 search_arxiv / search_surveys 从多个角度（broad / survey / method / benchmark / application）铺开候选。\n"
    "3. 只对真正有代表性的 2-3 篇论文调用 fetch_paper_fulltext，再用 retrieve_from_fulltext 逐篇取证据。\n"
    "4. 每篇代表论文都要有属于它自己的证据片段，不要让多篇论文共用同一批片段。\n"
    "5. 证据足够支撑写报告时调用 finish，并说明理由；不要无意义地重复同一个查询。"
)

PAPER_QA_AGENT_SYSTEM = (
    "你是一个论文问答 agent，可以自主调用工具在本地论文库中取证。\n"
    "工作原则：\n"
    "1. 先把问题里的指代（这篇/上面提到的）替换成明确对象，再调用 search_local_papers。\n"
    "2. 首轮证据不足或答非所问时，换一个更具体的检索表述再试一轮。\n"
    "3. 需要通读某个文件时才调用 read_local_file。\n"
    "4. 证据足够回答问题时调用 finish 并说明理由。"
)


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
        f"{TASK_PLANNER}\n"
        f"当前模式：{mode}\n"
        f"用户问题：{query}\n"
        f"用户偏好：{user_profile}\n"
        f"选中技能：{skill.get('name', '未选中')}\n"
        f"技能说明：{skill.get('description', '无')}\n"
        f"技能步骤：{skill.get('steps', [])}\n\n"
        f"上下文如下：\n{_format_context_layers(initial_question, middle_summary, recent_messages or [])}\n\n"
        "请输出简洁执行规划，说明应该如何检索、分析和组织回答。"
    )


def get_query_rewrite_prompt(initial_question: str, middle_summary: str, recent_messages: list[dict[str, str]], current_question: str) -> str:
    return (
        f"{TASK_QUERY_REWRITE}\n"
        "请把当前问题改写成适合检索的独立查询，补全代词指代，但不要捏造事实。\n\n"
        f"{_format_context_layers(initial_question, middle_summary, recent_messages)}\n\n"
        f"当前问题：{current_question}\n"
        "只输出改写后的检索查询。"
    )


def get_search_plan_prompt(topic: str, user_profile: dict[str, Any], long_term_memories: list[dict[str, Any]]) -> str:
    return (
        f"{TASK_SEARCH_PLAN}\n"
        f"研究主题：{topic}\n"
        f"用户偏好：{user_profile}\n"
        f"相关长期记忆：{_format_memories(long_term_memories)}\n\n"
        "请输出一个 JSON 对象，包含："
        "goal（调研目标）、subtopics（子主题列表）、query_groups（搜索组名和说明）、open_questions（待回答问题列表）、"
        "selection_criteria（筛选论文标准列表）、report_outline（建议综述章节列表，4-6 个中文章节标题）。\n"
        "report_outline 会被直接用作最终报告的章节结构，请结合本主题实际情况定制，不要套用通用模板。"
    )


def get_query_batch_prompt(topic: str, search_plan: dict[str, Any]) -> str:
    return (
        f"{TASK_QUERY_BATCH}\n"
        f"研究主题：{topic}\n"
        f"调研目标：{search_plan.get('goal', '未指定')}\n"
        f"子主题：{search_plan.get('subtopics', [])}\n"
        f"搜索组：{search_plan.get('query_groups', [])}\n\n"
        "请输出一个 JSON 数组，每个元素包含 label 和 query 两个字段，"
        "覆盖 broad、survey、method、benchmark、application 等不同角度。"
        "query 必须是英文检索词（arXiv 只索引英文），不要直接使用中文原句。"
    )


def get_topic_agent_goal_prompt(
    topic: str,
    search_plan: dict[str, Any],
    query_batches: list[dict[str, Any]],
    open_questions: list[str],
    evidence_gaps: list[str],
    round_index: int,
) -> str:
    """交给 tool calling 循环的取证目标。"""
    lines = [
        TASK_TOPIC_AGENT,
        f"研究主题：{topic}",
        f"调研目标：{search_plan.get('goal', '未指定')}",
        f"子主题：{search_plan.get('subtopics', [])}",
        f"论文筛选标准：{search_plan.get('selection_criteria', [])}",
        f"计划中的检索角度：{[item.get('label') for item in query_batches]}",
        f"建议起始 query：{[item.get('query') for item in query_batches][:6]}",
    ]
    if open_questions:
        lines.append(f"待回答问题：{open_questions}")
    if round_index > 1:
        lines.append(f"这是第 {round_index} 轮取证。上一轮被判定存在以下证据缺口，请优先补齐：{evidence_gaps}")
        lines.append("请换用不同的检索表述或不同角度，不要重复上一轮已经跑过的 query。")
    lines.append("请自主调用工具完成取证，最后调用 finish。")
    return "\n".join(lines)


def get_paper_qa_agent_goal_prompt(
    question: str,
    rewritten_query: str,
    active_document: dict[str, Any],
    evidence_gaps: list[str],
    round_index: int,
) -> str:
    lines = [
        TASK_PAPER_QA_AGENT,
        f"用户问题：{question}",
        f"建议检索查询：{rewritten_query or question}",
        f"当前锁定文档：{active_document.get('file_path') or '无（在本地论文库范围内检索）'}",
    ]
    if round_index > 1 and evidence_gaps:
        lines.append(f"这是第 {round_index} 轮取证，上一轮的证据缺口：{evidence_gaps}")
    lines.append("请自主调用工具完成取证，最后调用 finish。")
    return "\n".join(lines)


def get_reflection_prompt(
    query: str,
    intent_mode: str,
    open_questions: list[str],
    notes: str,
    citations: list[dict[str, Any]],
    evidence_status: list[dict[str, Any]],
    round_index: int,
    max_rounds: int,
) -> str:
    citation_lines = (
        "\n".join(
            f"- {item.get('id')}: {item.get('source_name')} (page={item.get('page')}, chunk={item.get('chunk_index')})"
            for item in citations[:12]
        )
        or "无"
    )
    status_lines = (
        "\n".join(
            f"- {item.get('title', 'unknown')}: 证据来源={item.get('evidence_status', 'unknown')}，命中片段={item.get('evidence_count', 0)}"
            for item in evidence_status
        )
        or "无"
    )
    return (
        f"{TASK_REFLECTION}\n"
        f"当前任务：{query}\n"
        f"任务类型：{intent_mode}\n"
        f"当前是第 {round_index} 轮取证，最多允许 {max_rounds} 轮。\n\n"
        f"计划中待回答的问题：\n{open_questions or '无'}\n\n"
        f"已有证据编号：\n{citation_lines}\n\n"
        f"每篇代表论文的证据归属情况：\n{status_lines}\n\n"
        f"已整理的研究笔记（截断）：\n{notes[:3000]}\n\n"
        "请判断现有证据是否足够支撑写出可溯源的结论，并输出 JSON 对象，字段包括：\n"
        "- sufficient（布尔值，证据是否已足够）\n"
        "- evidence_gaps（字符串数组，仍然缺失的具体证据；sufficient 为 true 时给空数组）\n"
        "- open_questions（字符串数组，仍未回答的问题）\n"
        "- next_queries（字符串数组，若需要再搜一轮，建议的英文检索词，最多 4 个）\n"
        "- reason（一句话说明判断依据）\n"
        "判定标准从严：若某篇代表论文的证据来源是 fallback（没有属于它自己的片段），"
        "或多个待回答问题完全没有对应证据，就应判为 sufficient=false。"
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
    task_plan: str = "",
) -> str:
    return (
        f"{TASK_RESEARCH_NOTES}\n"
        "请基于检索资料生成结构化研究笔记。\n"
        f"模式：{mode}\n"
        f"当前问题：{query}\n"
        f"技能步骤：{skill_steps}\n"
        f"本轮执行规划（由规划节点产出，请据此组织笔记）：\n{task_plan or '无'}\n"
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
    depth = user_profile.get("depth", "")
    file_hint = active_document.get("file_path", "")
    survey_hint = ""
    if survey_artifact and survey_artifact.get("outline"):
        survey_hint = f"请严格按以下章节结构组织报告，章节标题保持一致：{survey_artifact.get('outline', [])}\n"
    return (
        f"{TASK_FINAL_ANSWER}\n"
        "请根据研究笔记回答最新问题。\n"
        f"模式：{mode}\n"
        f"当前问题：{query}\n"
        f"输出语言：{language}\n"
        f"输出格式：{output_format}\n"
        f"写作风格：{tone}\n"
        + (f"内容深度：{depth}\n" if depth else "")
        + f"当前锁定文件：{file_hint or '无'}\n"
        f"{survey_hint}"
        f"{_format_context_layers(initial_question, middle_summary, recent_messages)}\n\n"
        "以下是研究笔记：\n"
        f"{notes}\n\n"
        "请直接回答当前问题；如果涉及多篇论文，请区分每篇论文的结论；"
        "每个核心论断尽量保留并引用 [Sx]、page、chunk 位置；"
        "只能使用研究笔记里真实出现过的 [Sx] 编号，不要自己编号；"
        "如果证据不足请明确说明；最后附上 Sources 小节。"
    )


def get_paper_note_prompt(topic: str, paper: dict[str, Any]) -> str:
    return (
        f"{TASK_PAPER_NOTE}\n"
        f"研究主题：{topic}\n"
        f"论文标题：{paper.get('title', '')}\n"
        f"论文摘要：{paper.get('summary', '')}\n"
        "请用中文输出 JSON 对象，字段包括：problem, related_work, method, experiments, summary。"
    )


def get_fulltext_note_prompt(topic: str, paper: dict[str, Any], evidence_text: str) -> str:
    return (
        f"{TASK_FULLTEXT_NOTE}\n"
        f"研究主题：{topic}\n"
        f"论文标题：{paper.get('title', '')}\n"
        f"arXiv ID：{paper.get('arxiv_id', '')}\n"
        f"以下是该论文全文检索出的关键证据：\n{evidence_text}\n\n"
        "请用中文输出 JSON 对象，字段包括：problem, method, experiments, strengths, limitations, summary。"
        "每个字段都基于上面的证据作答，并在句末保留对应的 [Sx] 编号；证据不足的字段写\"证据不足\"。"
    )


def get_survey_outline_prompt(topic: str, working_document: dict[str, Any]) -> str:
    candidate_titles = [note.get("title", "") for note in working_document.get("paper_notes_brief", [])][:8]
    return (
        f"{TASK_SURVEY_OUTLINE}\n"
        f"研究主题：{topic}\n"
        f"已检索到的候选论文：{candidate_titles}\n"
        f"待回答问题：{working_document.get('open_questions', [])}\n\n"
        "请输出 JSON 数组，每一项是一节综述标题（中文，4-6 节），"
        "要求覆盖问题背景、代表方法、实验对比、研究空白与未来方向，并结合本主题的实际情况定制。"
    )


def get_survey_section_prompt(topic: str, section_title: str, evidence_text: str, working_document: dict[str, Any]) -> str:
    return (
        f"{TASK_SURVEY_SECTION}\n"
        f"研究主题：{topic}\n"
        f"当前章节：{section_title}\n"
        f"综述整体章节：{working_document.get('report_outline', [])}\n"
        f"代表论文：{[note.get('title', '') for note in working_document.get('paper_notes_fulltext', [])]}\n"
        f"证据与笔记：\n{evidence_text}\n\n"
        f"请只撰写「{section_title}」这一节的正文（不要重复写其他章节，不要写标题行）。"
        "要求引用证据编号 [Sx]，明确区分不同论文的贡献与差异；只能使用上面真实出现过的编号；证据不足时明确说明。"
    )


def get_middle_summary_prompt(initial_question: str, middle_messages: list[dict[str, str]]) -> str:
    transcript = "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in middle_messages)
    return (
        f"{TASK_MIDDLE_SUMMARY}\n"
        "以下是一段多轮学术研究对话的中间部分，需要压缩成简洁摘要以节省上下文。\n"
        f"用户最初的目标：{initial_question or '未知'}\n\n"
        f"中间对话原文：\n{transcript}\n\n"
        "压缩要求：\n"
        "1. 必须保留用户在这一段里提出的具体问题与关键事实/约束（含具体名词、数字、论文名）。\n"
        "2. 必须保留仍未解决的问题。\n"
        "3. 不要编造原文没有的内容，不要输出结论性评价。\n"
        "4. 输出中文，控制在 300 字以内，用「中间阶段用户主要问题：」和「中间阶段已回答要点：」两行组织。"
    )


def get_preference_extraction_prompt(task: str, report: str, current_preferences: dict[str, Any]) -> str:
    return (
        f"{TASK_PREFERENCES}\n"
        "请从用户的请求与本轮产出中推断用户的输出偏好。\n"
        f"用户请求：{task}\n"
        f"本轮产出（截断）：{report[:1200]}\n"
        f"当前已记录的偏好：{current_preferences}\n\n"
        "请输出 JSON 对象，仅可包含以下字段与取值：\n"
        '- language: "chinese" | "english"\n'
        '- format: "structured" | "table" | "markdown" | "bullet" | "narrative"\n'
        '- tone: "academic" | "concise" | "neutral" | "tutorial"\n'
        '- depth: "overview" | "standard" | "deep"\n'
        "- interests: 字符串数组，用户关注的研究方向关键词（小写，最多 8 个）\n"
        "没有把握的字段直接省略，不要输出取值范围之外的值，不要输出解释文字。"
    )


def get_memory_selection_prompt(topic: str, notes: list[dict[str, Any]], final_answer: str = "", user_profile: dict[str, Any] | None = None) -> str:
    return (
        f"{TASK_MEMORY_SELECTION}\n"
        f"当前研究主题：{topic}\n"
        f"候选研究笔记：{notes}\n"
        f"最终回答：{final_answer}\n"
        f"用户偏好：{user_profile or {}}\n\n"
        "请输出一个 JSON 数组，每个元素包含 memory_type 和 content 两个字段。"
        "每条内容都要简短、可长期复用，适合保存为用户偏好或研究摘要。最多输出 5 条。"
    )
