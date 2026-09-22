"""FastAPI 适配层：把阻塞的 run_research 包装成 submit → SSE 进度 → 取结果的异步形态。

不改动 agent 核心逻辑，纯增量。设计见 note/web_stack_plan.md。
"""
