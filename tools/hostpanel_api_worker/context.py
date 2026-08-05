from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Callable

from .model import JobCancelled, JobLike, LeaseLost
from .store import JobStore

_ALLOWED_LEVELS = frozenset({"debug", "info", "warning", "error"})


class JobContext:
    __slots__ = ("_job", "_store", "_worker_id", "_lease_seconds", "_now")

    def __init__(
        self,
        job: JobLike,
        *,
        store: JobStore,
        worker_id: str,
        lease_seconds: int,
        now: Callable[[], int],
    ) -> None:
        self._job = job
        self._store = store
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._now = now

    @property
    def job_id(self) -> str:
        return self._job.job_id

    @property
    def tenant_id(self) -> str:
        return self._job.tenant_id

    @property
    def job_type(self) -> str:
        return self._job.job_type

    @property
    def attempts(self) -> int:
        return self._job.attempts

    @property
    def progress(self) -> int:
        return self._job.progress

    @property
    def payload(self) -> Mapping[str, Any]:
        return freeze_mapping(self._job.payload)

    def checkpoint(self, progress: int | None = None) -> None:
        if self._job.cancel_requested:
            raise JobCancelled("job cancellation requested")
        next_progress = self._job.progress if progress is None else progress
        if (
            not isinstance(next_progress, int)
            or isinstance(next_progress, bool)
            or not self._job.progress <= next_progress <= 100
        ):
            raise ValueError("progress is invalid or moved backwards")
        try:
            self._job = self._store.heartbeat_job(
                self._job.job_id,
                worker_id=self._worker_id,
                progress=next_progress,
                lease_seconds=self._lease_seconds,
                now=self._now(),
            )
        except RuntimeError as exc:
            raise LeaseLost("job lease is invalid") from exc
        if self._job.cancel_requested:
            raise JobCancelled("job cancellation requested")

    def log(self, level: str, message: str) -> None:
        if level not in _ALLOWED_LEVELS:
            raise ValueError("job log level is invalid")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message.encode("utf-8")) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in message)
        ):
            raise ValueError("job log message is invalid")
        try:
            self._store.append_job_log(
                self._job.job_id,
                worker_id=self._worker_id,
                level=level,
                message=message,
                now=self._now(),
            )
        except RuntimeError as exc:
            raise LeaseLost("job lease is invalid") from exc


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("job payload must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("job payload contains a non-text key")
    return MappingProxyType({key: _freeze(child) for key, child in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("job payload contains a non-text key")
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(child) for child in value)
    return value
