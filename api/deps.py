from __future__ import annotations

from fastapi import Request

from api.jobs import JobManager


def get_job_manager(request: Request) -> JobManager:
    """从 app.state 取 JobManager 单例（在 create_app 里创建、lifespan 里 bind_loop）。

    用 app.state 而非模块级全局：多个 create_app() 实例（例如测试）互不污染。
    """
    return request.app.state.job_manager
