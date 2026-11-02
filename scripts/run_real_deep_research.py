"""受控真实验收；仅输出计数与阶段，不保存配置、请求正文或异常原文。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUERY = "调研deep research如何进行测试，现有的研究如何衡量一个deep research框架/工具的能力，使用的数据集是什么，用什么评判标准，如何衡量"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--deadline", type=float, default=2700)
    parser.add_argument("--receipt", default="data/reports/deep-research-live-receipt.json")
    args = parser.parse_args()
    from config import apply_config_values, load_kv_config_file, OpenAICompatibleLLM, OpenAICompatibleEmbedder
    from agent.topic_scheduler import TopicScheduler

    apply_config_values(load_kv_config_file(args.config_file))
    os.environ["RESEARCH_AGENT_FAKE"] = "0"
    os.environ["RESEARCH_AGENT_LLM_COMPRESSION"] = "0"
    os.environ["ARXIV_MIN_REQUEST_INTERVAL"] = "3"
    session = "real-deep-research-eval-" + time.strftime("%Y%m%d-%H%M%S")
    receipt = ROOT / args.receipt
    receipt.parent.mkdir(parents=True, exist_ok=True)
    counts = {"events": 0, "arxiv_errors": 0, "llm_retries": 0}
    io_state = {"llm_calls": 0, "embedding_calls": 0, "active_io": "", "last_io_error": ""}
    start = time.monotonic()
    guard = threading.Lock()
    scheduler = None
    status = "starting"
    failure = ""

    def save():
        snapshot = scheduler.get_state_snapshot() if scheduler else {}
        with guard:
            topics = snapshot.get("topics", [])
            data = {"session_id": session, "query": QUERY, "status": status,
                    "phase": snapshot.get("phase", "initializing"),
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "topics": [{"id": t["id"], "status": t["status"]} for t in topics],
                    "selected_papers": len(snapshot.get("selected_papers", [])),
                    "citations": len(snapshot.get("citations", [])),
                    "failure_type": failure, **counts, **io_state}
            report = str(snapshot.get("report_path") or "")
            if report:
                try:
                    data["report_path"] = Path(report).relative_to(ROOT).as_posix()
                except ValueError:
                    pass
            stage = receipt.with_suffix(".tmp")
            stage.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            stage.replace(receipt)

    def event(ev):
        message = str(ev.get("message", ""))
        with guard:
            counts["events"] += 1
            if "LLM 暂时不可用" in message:
                counts["llm_retries"] += 1
            if "arXiv" in message and any(s in message for s in ("异常", "失败", "HTTP", "无法解析")):
                counts["arxiv_errors"] += 1

    def track_io(original, kind):
        def tracked(*values, **options):
            with guard:
                io_state[f"{kind}_calls"] += 1
                io_state["active_io"] = kind
            try:
                return original(*values, **options)
            except Exception as exc:
                with guard:
                    io_state["last_io_error"] = type(exc).__name__
                raise
            finally:
                with guard:
                    io_state["active_io"] = ""
        return tracked

    # 仅观测类型与计数，调用参数和返回正文不进入回执。
    OpenAICompatibleLLM._create = track_io(OpenAICompatibleLLM._create, "llm")
    OpenAICompatibleEmbedder.embed_documents = track_io(OpenAICompatibleEmbedder.embed_documents, "embedding")
    OpenAICompatibleEmbedder.embed_query = track_io(OpenAICompatibleEmbedder.embed_query, "embedding")

    scheduler = TopicScheduler(session_id=session, initial_query=QUERY, top_k=4,
                               fake_mode=False, progress_event_callback=event)
    completed = threading.Event()

    def run():
        nonlocal status, failure
        try:
            scheduler.start()
            scheduler.wait_until_complete()
            status = scheduler.phase.value
        except Exception as exc:
            failure = type(exc).__name__
            status = "failed"
        finally:
            completed.set()

    worker = threading.Thread(target=run, daemon=True)
    status = "running"
    worker.start()
    while not completed.wait(5):
        save()
        if time.monotonic() - start > args.deadline:
            scheduler.cancel_run("真实验收达到时间预算")
            failure = "ValidationDeadline"
            status = "timed_out"
            break
    save()
    scheduler.shutdown()
    print(json.dumps({"receipt": args.receipt, "status": status}, ensure_ascii=False))
    if status != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
