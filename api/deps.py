from __future__ import annotations

from fastapi import Request

from api.jobs import JobManager


def get_job_manager(request: Request) -> JobManager:

    return request.app.state.job_manager
