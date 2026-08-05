from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .model import JobLike


class JobStore(Protocol):
    def claim_job(self, *, worker_id: str, lease_seconds: int, now: int | None = None) -> JobLike | None: ...

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: int,
        lease_seconds: int,
        now: int | None = None,
    ) -> JobLike: ...

    def append_job_log(
        self,
        job_id: str,
        *,
        worker_id: str,
        level: str,
        message: str,
        now: int | None = None,
    ) -> object: ...

    def complete_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
        now: int | None = None,
    ) -> JobLike: ...

    def fail_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: int | None = None,
        now: int | None = None,
    ) -> JobLike: ...
