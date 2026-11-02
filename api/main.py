from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.jobs import JobManager
from api.routes import health, jobs, papers, sessions, settings
from config import ensure_directories, get_settings
from storage import ChromaOutboxWorker, Database

# 部署形态：前端 build 产物（web/dist）由 FastAPI 托管，单进程单容器、线上不需要 Node（阶段④）。
WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # run_research 在工作线程里跑，事件要投递回这个 event loop：阻塞调用若直接进 event loop 会卡死全部请求。
    app.state.job_manager.bind_loop(asyncio.get_running_loop())
    runtime_settings = get_settings()
    ensure_directories(runtime_settings)
    app.state.storage_db = Database(runtime_settings["sqlite_db_path"])
    app.state.chroma_worker = ChromaOutboxWorker(
        app.state.storage_db,
        runtime_settings["chroma_dir"],
        poll_interval=runtime_settings["outbox_poll_interval"],
        stale_after=runtime_settings["outbox_stale_after"],
    ).start()
    try:
        yield
    finally:
        app.state.chroma_worker.stop()
        if not app.state.chroma_worker.is_alive:
            app.state.storage_db.close()
        app.state.job_manager.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Research Agent Web API", version="0.2.0", lifespan=lifespan)
    # 单进程单用户：max_workers 小，并把建索引类任务串行化：Chroma 不支持多进程写同一目录。
    app.state.job_manager = JobManager(max_workers=2)
    app.include_router(sessions.router)
    app.include_router(jobs.router)
    app.include_router(papers.router)
    app.include_router(health.router)
    app.include_router(settings.router)
    if WEB_DIST_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST_DIR, html=True), name="web")
    return app


# 开发/生产入口：uvicorn api.main:app
app = create_app()
