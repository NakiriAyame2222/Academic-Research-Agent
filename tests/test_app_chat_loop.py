from __future__ import annotations

import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


def test_run_chat_loop_keeps_session_alive_after_initial_query(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []
    saved_states: list[dict[str, object]] = []

    responses = iter(["追加问题", "exit"])

    def fake_input(prompt: str = "") -> str:
        return next(responses)

    def fake_run_research(**kwargs):
        calls.append(kwargs)
        return {
            "final_answer": f"answer:{kwargs['query']}",
            "paper_path": "",
            "session_id": kwargs["session_id"],
            "pending_memory_candidates": [{"memory_type": "summary", "content": "候选记忆"}],
        }

    def fake_save_pending_memories(state, user_id, settings, input_fn=None, confirm_fn=None):
        saved_states.append({"state": state, "user_id": user_id, "settings": settings})
        return True

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(app, "run_research", fake_run_research)
    monkeypatch.setattr(app, "maybe_save_pending_memories", fake_save_pending_memories)
    monkeypatch.setattr(app, "save_checkpoint", lambda state, session_id=None: None)
    monkeypatch.setattr(app, "get_settings", lambda: {"sqlite_db_path": "dummy.db"})

    app.run_chat_loop(
        user_id="u1",
        session_id="s1",
        file_path=None,
        rebuild_index=False,
        top_k=None,
        resume=False,
        initial_query="初始研究主题",
    )

    output = capsys.readouterr().out
    assert "进入学术研究对话模式" in output
    assert "answer:初始研究主题" in output
    assert "answer:追加问题" in output
    assert "已保存你选择的长期记忆。" in output
    assert "已退出。" in output

    assert [call["query"] for call in calls] == ["初始研究主题", "追加问题"]
    assert calls[0]["resume"] is False
    assert calls[1]["resume"] is True
    assert len(saved_states) == 1
    assert saved_states[0]["user_id"] == "u1"


def test_run_chat_loop_without_initial_query_prompts_for_first_turn(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []
    responses = iter(["研究主题", "exit"])

    def fake_input(prompt: str = "") -> str:
        return next(responses)

    def fake_run_research(**kwargs):
        calls.append(kwargs)
        return {
            "final_answer": "answer:研究主题",
            "paper_path": "",
            "session_id": kwargs["session_id"],
            "pending_memory_candidates": [],
        }

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(app, "run_research", fake_run_research)
    monkeypatch.setattr(app, "maybe_save_pending_memories", lambda *args, **kwargs: False)
    monkeypatch.setattr(app, "save_checkpoint", lambda state, session_id=None: None)
    monkeypatch.setattr(app, "get_settings", lambda: {"sqlite_db_path": "dummy.db"})

    app.run_chat_loop(
        user_id="u1",
        session_id="s1",
        file_path=None,
        rebuild_index=False,
        top_k=None,
        resume=False,
    )

    output = capsys.readouterr().out
    assert "answer:研究主题" in output
    assert "未保存长期记忆。" in output
    assert len(calls) == 1
    assert calls[0]["query"] == "研究主题"
