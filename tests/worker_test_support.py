from __future__ import annotations

import dataclasses
import pathlib
import sys
import unittest
from collections import deque
from collections.abc import Mapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_worker import (  # noqa: E402
    HandlerContractError,
    HandlerRegistry,
    JobCancelled,
    JobContext,
    LeaseLost,
    RegistryError,
    RetryableJobError,
    RunOutcome,
    WorkerConfig,
    WorkerRunner,
    WorkerService,
)

NOW = 2_000_000_000


@dataclasses.dataclass(frozen=True, slots=True)
class FakeJob:
    job_id: str
    tenant_id: str
    job_type: str
    status: str = "pending"
    payload: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    cancel_requested: bool = False
    progress: int = 0
    lease_owner: str | None = None
    lease_expires_at: int | None = None


class FakeStore:
    def __init__(self, jobs=()):
        self.queue = deque(jobs)
        self.jobs = {job.job_id: job for job in jobs}
        self.logs: list[tuple[str, str, str, int]] = []
        self.calls: list[tuple] = []
        self.fail_operation: str | None = None
        self.cancel_on_heartbeat = False
        self.claim_error = False

    def claim_job(self, *, worker_id, lease_seconds, now=None):
        self.calls.append(("claim", worker_id, lease_seconds, now))
        if self.claim_error:
            raise RuntimeError("database unavailable")
        if not self.queue:
            return None
        job = self.queue.popleft()
        running = dataclasses.replace(
            job,
            status="running",
            attempts=job.attempts + 1,
            lease_owner=worker_id,
            lease_expires_at=now + lease_seconds,
        )
        self.jobs[job.job_id] = running
        return running

    def heartbeat_job(self, job_id, *, worker_id, progress, lease_seconds, now=None):
        self.calls.append(("heartbeat", job_id, worker_id, progress, lease_seconds, now))
        self._maybe_fail("heartbeat")
        job = self._leased(job_id, worker_id, now)
        updated = dataclasses.replace(
            job,
            progress=progress,
            cancel_requested=job.cancel_requested or self.cancel_on_heartbeat,
            lease_expires_at=now + lease_seconds,
        )
        self.jobs[job_id] = updated
        return updated

    def append_job_log(self, job_id, *, worker_id, level, message, now=None):
        self.calls.append(("log", job_id, worker_id, level, message, now))
        self._maybe_fail("log")
        self._leased(job_id, worker_id, now)
        self.logs.append((job_id, level, message, now))
        return object()

    def complete_job(self, job_id, *, worker_id, result=None, now=None):
        self.calls.append(("complete", job_id, worker_id, result, now))
        self._maybe_fail("complete")
        job = self._leased(job_id, worker_id, now)
        updated = dataclasses.replace(
            job,
            status="cancelled" if job.cancel_requested else "succeeded",
            progress=job.progress if job.cancel_requested else 100,
            lease_owner=None,
            lease_expires_at=None,
        )
        object.__setattr__(updated, "result", result) if hasattr(updated, "result") else None
        self.jobs[job_id] = updated
        return updated

    def fail_job(self, job_id, *, worker_id, error, retry_at=None, now=None):
        self.calls.append(("fail", job_id, worker_id, error, retry_at, now))
        self._maybe_fail("fail")
        job = self._leased(job_id, worker_id, now)
        if job.cancel_requested:
            status = "cancelled"
        elif retry_at is not None and job.attempts < job.max_attempts:
            status = "retry_wait"
        else:
            status = "failed"
        updated = dataclasses.replace(job, status=status, lease_owner=None, lease_expires_at=None)
        self.jobs[job_id] = updated
        return updated

    def _leased(self, job_id, worker_id, now):
        job = self.jobs[job_id]
        if (
            job.status != "running"
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at < now
        ):
            raise RuntimeError("job lease is invalid")
        return job

    def _maybe_fail(self, operation):
        if self.fail_operation == operation:
            raise RuntimeError(f"{operation} failed")


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        current = self.value
        self.value += 1
        return current


def job(job_type="site.rebuild", **overrides):
    values = {
        "job_id": "job_ABCDEFGHIJKLMNOPQRSTUV",
        "tenant_id": "tenant-a",
        "job_type": job_type,
        "payload": {"site_id": "site-a", "nested": {"values": [1, 2]}},
    }
    values.update(overrides)
    return FakeJob(**values)


def runner(store, handlers=None, *, reporter=None, config=None, clock=None):
    registry = HandlerRegistry(handlers or {"site.rebuild": lambda context, payload: {"ok": True}})
    return WorkerRunner(
        store,
        registry,
        config or WorkerConfig("worker-a", lease_seconds=30),
        clock=clock or Clock(),
        error_reporter=reporter,
    )
