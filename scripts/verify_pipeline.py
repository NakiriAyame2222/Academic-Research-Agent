"""端到端验证脚本：真实 arXiv + 真实 embedding + 可控的本地 LLM 替身。

用途：cong.txt 的 LLM 中转端点不可用时，仍能验证整条链路
（意图路由 -> planner -> tool calling 循环 -> 全文索引 -> 证据归属 ->
反思回边 -> 大纲接线 -> 逐节撰写 -> 引用校验 -> 报告落盘）。

    python scripts/verify_pipeline.py --config-file cong.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.nodes as nodes_module
import agent.tool_loop as tool_loop_module
from agent import runtime as app_module
import prompts
from config import apply_config_values, load_kv_config_file


class ScriptedToolCallingLLM:
    """按脚本发出真实 tool_calls，用来驱动 agent 的取证循环。"""

    def __init__(self, topic_query: str) -> None:
        self.topic_query = topic_query
        self.step = 0
        self.calls: list[str] = []

    def __call__(self, messages, tools=None, tool_choice="auto"):
        names = [tool["function"]["name"] for tool in (tools or [])]
        self.step += 1
        if self.step == 1:
            call = {"id": "c1", "name": "search_arxiv", "arguments": json.dumps({"query": self.topic_query, "max_results": 4, "label": "broad"})}
        elif self.step == 2:
            call = {"id": "c2", "name": "search_arxiv", "arguments": json.dumps({"query": f"{self.topic_query} benchmark", "max_results": 3, "label": "benchmark"})}
        elif self.step == 3:
            call = {"id": "c3", "name": "fetch_paper_fulltext", "arguments": json.dumps({"limit": 2})}
        elif self.step == 4:
            call = {"id": "c4", "name": "retrieve_from_fulltext", "arguments": json.dumps({"query": self.topic_query, "top_k": 8})}
        else:
            call = {"id": "c5", "name": "finish", "arguments": json.dumps({"reason": "已覆盖多角度检索并取到全文证据"})}
        assert call["name"] in names, f"{call['name']} not in {names}"
        self.calls.append(call["name"])
        return {"content": "", "tool_calls": [call], "degraded": False}


def make_stub_invoke(topic: str):
    """本地生成各阶段的 LLM 输出，故意混入 markdown 围栏和一个伪造引用编号。"""
    outline = ["研究背景与动机", "代表方法与技术路线", "评测基准与实验结论", "开放问题与未来方向"]

    def stub(prompt: str, system: str | None = None, json_object: bool = False) -> str:
        if prompts.TASK_SEARCH_PLAN in prompt:
            return (
                "```json\n"
                + json.dumps(
                    {
                        "goal": f"梳理 {topic} 的核心问题、代表方法与评测",
                        "subtopics": [f"{topic} methods", f"{topic} evaluation"],
                        "query_groups": ["broad", "benchmark"],
                        "open_questions": ["有哪些公认的评测基准？"],
                        "selection_criteria": ["代表性", "实验充分性"],
                        "report_outline": outline,
                    },
                    ensure_ascii=False,
                )
                + "\n```"
            )
        if prompts.TASK_QUERY_BATCH in prompt:
            return json.dumps(
                [{"label": "broad", "query": f"{topic} methods"}, {"label": "benchmark", "query": f"{topic} benchmark"}],
                ensure_ascii=False,
            )
        if prompts.TASK_PAPER_NOTE in prompt:
            return '{"problem": "问题定义", "related_work": "相关工作", "method": "方法", "experiments": "实验", "summary": "候选论文摘要笔记"}'
        if prompts.TASK_FULLTEXT_NOTE in prompt:
            return '{"problem": "后验推断困难 [S1]", "method": "变分/采样方法 [S1]", "experiments": "在若干基准上评估 [S2]", "strengths": "可扩展", "limitations": "长尾场景不足", "summary": "该论文给出可复现的方法与实验 [S1]"}'
        if prompts.TASK_REFLECTION in prompt:
            return '{"sufficient": true, "evidence_gaps": [], "open_questions": [], "next_queries": [], "reason": "每篇代表论文均有专属证据"}'
        if prompts.TASK_SURVEY_SECTION in prompt:
            section = prompt.split("当前章节：")[1].split("\n")[0]
            # 逐节撰写时按证据文本里出现的编号引用，验证跨节引用编号一致
            fake = " 另有一条无来源论断 [S99]。" if "未来" in section else ""
            return f"围绕「{section}」的分析：结合检索到的全文证据展开 [S1]，并与代表论文对比 [S4]、[S7]。{fake}"
        if prompts.TASK_QUERY_REWRITE in prompt:
            return topic
        return f"关于 {topic} 的阶段性产出。"

    return stub


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", default="cong.txt")
    parser.add_argument("--topic", default="latent variable reasoning")
    parser.add_argument("--query", default="搜索并总结现有的关于潜变量推理的研究,返回一个报告")
    args = parser.parse_args()

    apply_config_values(load_kv_config_file(args.config_file))
    # 本机系统代理把 https 代理写成了 https:// scheme，httpx 会直接 TLS 失败
    os.environ.setdefault("RESEARCH_AGENT_PROXY", "none")
    os.environ["RESEARCH_AGENT_TOOL_CALLING"] = "1"
    os.environ["RESEARCH_AGENT_LLM_COMPRESSION"] = "0"

    print(f"[1] 意图路由：{args.query!r} -> {app_module.detect_intent_mode(args.query)}\n")

    scripted = ScriptedToolCallingLLM(args.topic)
    tool_loop_module.tool_calling_available = lambda: True
    tool_loop_module.chat_with_tools = scripted
    nodes_module._invoke_llm = make_stub_invoke(args.topic)

    final_state = app_module.run_research(
        query=args.query,
        user_id="verify",
        session_id="verify-pipeline",
        rebuild_index=False,
        top_k=4,
        resume=False,
        progress_callback=lambda line: print(line, flush=True),
        output_path=str(ROOT / "data" / "reports" / "verify_pipeline.md"),
    )

    trace_tools = [item["tool"] for item in final_state["tool_trace"]]
    note = (final_state.get("paper_notes") or [{}])[0]
    report = Path(final_state["report_path"]) if final_state.get("report_path") else None

    print("\n================ 验证结果 ================")
    print(f"intent_mode            : {final_state['intent_mode']}")
    print(f"planner report_outline : {final_state['search_plan'].get('report_outline')}")
    print(f"实际报告章节           : {final_state['survey_artifact'].get('outline')}")
    print(f"tool 调用顺序          : {trace_tools}")
    print(f"tool_loop_degraded     : {final_state['tool_loop_degraded']}")
    print(f"候选论文数             : {len(final_state['paper_candidates'])}")
    print(f"代表论文               : {[p.get('title', '')[:55] for p in final_state['selected_papers']]}")
    print(f"证据归属               : {[(i['arxiv_id'], i['evidence_status'], i['evidence_count']) for i in final_state['evidence_status']]}")
    print(f"引用条目数             : {len(final_state['citations'])}")
    print(f"引用校验               : {final_state['citation_audit']}")
    print(f"研究轮数               : {final_state['research_rounds']}")
    print(f"精读笔记 method 字段   : {note.get('method')!r}")
    print(f"笔记 summary 是裸 JSON : {str(note.get('summary', '')).strip().startswith('{')}")
    print(f"报告落盘               : {final_state.get('report_path')}")

    checks = {
        "意图判定为 topic_research": final_state["intent_mode"] == "topic_research",
        "tool calling 未降级": final_state["tool_loop_degraded"] is False,
        "真实调用了 arXiv 搜索": "search_arxiv" in trace_tools,
        "下载并索引了全文": "fetch_paper_fulltext" in trace_tools,
        "带围栏的搜索计划已解析": final_state["search_plan"].get("goal", "").startswith("梳理"),
        "报告章节来自 planner": final_state["survey_artifact"].get("outline") == final_state["search_plan"].get("report_outline"),
        "每篇论文有专属证据": bool(final_state["evidence_status"])
        and all(i["evidence_status"] != "fallback" for i in final_state["evidence_status"]),
        "精读笔记已结构化": bool(note.get("method")) and not str(note.get("summary", "")).strip().startswith("{"),
        "抓出伪造引用 S99": "S99" in final_state["citation_audit"].get("invalid_references", []),
        "报告已落盘": bool(report and report.exists() and report.stat().st_size > 0),
    }
    print("\n---------------- 断言 ----------------")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("\n全部通过" if all(checks.values()) else "\n存在未通过项")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
