from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

_WORKER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
_JOB_TYPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_PUBLIC_MESSAGE_RE = re.compile(r"[ -~]{1,256}\Z")


class WorkerError(RuntimeError):
    """Base error for the reviewed worker runtime."""


class RegistryError(WorkerError):
    """The fixed handler registry is malformed or frozen."""


class HandlerContractError(WorkerError):
    """A handler returned a value outside the reviewed contract."""


class LeaseLost(WorkerError):
    """The durable job lease can no longer be used by this worker."""


class JobCancelled(WorkerError):
    """The durable job has a cancellation request."""


class RetryableJobError(WorkerError):
    """A handler requests a bounded durable retry."""

    def __init__(self, message: str = "job failed temporarily", *, retry_after: int | None = None):
        super().__init__(public_message(message, fallback="job failed temporarily"))
        if retry_after is not None and (
            not isinstance(retry_after, int)
            or isinstance(retry_after, bool)
            or retry_after < 1
        ):
            raise HandlerContractError("retry_after must be a positive integer")
        self.public_message = public_message(message, fallback="job failed temporarily")
        self.retry_after = retry_after


@dataclasses.dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int = 60
    idle_sleep_seconds: float = 1.0
    max_jobs_per_cycle: int = 100
    retry_base_seconds: int = 5
    retry_max_seconds: int = 300

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or _WORKER_ID_RE.fullmatch(self.worker_id) is None:
            raise ValueError("worker_id is invalid")
        _bounded_int(self.lease_seconds, "lease_seconds", 1, 3600)
        if (
            not isinstance(self.idle_sleep_seconds, (int, float))
            or isinstance(self.idle_sleep_seconds, bool)
            or not 0.001 <= float(self.idle_sleep_seconds) <= 60.0
        ):
            raise ValueError("idle_sleep_seconds is invalid")
        _bounded_int(self.max_jobs_per_cycle, "max_jobs_per_cycle", 1, 10_000)
        _bounded_int(self.retry_base_seconds, "retry_base_seconds", 1, 3600)
        _bounded_int(self.retry_max_seconds, "retry_max_seconds", 1, 86_400)
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_base_seconds exceeds retry_max_seconds")


@dataclasses.dataclass(frozen=True, slots=True)
class RunOutcome:
    status: str
    job_id: str | None = None
    job_type: str | None = None
    attempts: int | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "idle",
            "succeeded",
            "retry_wait",
            "failed",
            "cancelled",
            "lease_lost",
        }:
            raise ValueError("outcome status is invalid")
        if self.status == "idle" and any(
            value is not None for value in (self.job_id, self.job_type, self.attempts)
        ):
            raise ValueError("idle outcome cannot identify a job")


@runtime_checkable
class JobLike(Protocol):
    job_id: str
    tenant_id: str
    job_type: str
    status: str
    payload: Mapping[str, Any]
    attempts: int
    max_attempts: int
    cancel_requested: bool
    progress: int
    lease_owner: str | None
    lease_expires_at: int | None


def validate_job_type(value: str) -> str:
    if not isinstance(value, str) or _JOB_TYPE_RE.fullmatch(value) is None:
        raise RegistryError("job type is invalid")
    return value


def public_message(value: str, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split())
    if _PUBLIC_MESSAGE_RE.fullmatch(cleaned) is None:
        return fallback
    return cleaned


def retry_delay(config: WorkerConfig, attempts: int, requested: int | None = None) -> int:
    _bounded_int(attempts, "attempts", 1, 100)
    if requested is not None:
        _bounded_int(requested, "retry_after", 1, config.retry_max_seconds)
        return requested
    exponent = min(attempts - 1, 30)
    return min(config.retry_base_seconds * (2**exponent), config.retry_max_seconds)


def _bounded_int(value: int, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{label} is invalid")
    return value
