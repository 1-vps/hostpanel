from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .compatibility import verify_store_contract
from .context import JobContext, freeze_mapping
from .model import (
    HandlerContractError,
    JobCancelled,
    JobLike,
    LeaseLost,
    RetryableJobError,
    RunOutcome,
    WorkerConfig,
    public_message,
    retry_delay,
)
from .registry import HandlerRegistry
from .store import JobStore

ErrorReporter = Callable[[JobLike, BaseException], None]


class WorkerRunner:
    __slots__ = ("store", "registry", "config", "_clock", "_reporter")

    def __init__(
        self,
        store: JobStore,
        registry: HandlerRegistry,
        config: WorkerConfig,
        *,
        clock: Callable[[], int],
        error_reporter: ErrorReporter | None = None,
    ) -> None:
        if not callable(clock):
            raise ValueError("clock must be callable")
        if error_reporter is not None and not callable(error_reporter):
            raise ValueError("error_reporter must be callable")
        verify_store_contract(store)
        self.store = store
        self.registry = registry
        self.config = config
        self._clock = clock
        self._reporter = error_reporter or (lambda job, error: None)

    def run_once(self) -> RunOutcome:
        now = self._clock()
        try:
            job = self.store.claim_job(
                worker_id=self.config.worker_id,
                lease_seconds=self.config.lease_seconds,
                now=now,
            )
        except RuntimeError:
            return RunOutcome("lease_lost")
        if job is None:
            return RunOutcome("idle")

        handler = self.registry.resolve(job.job_type)
        context = JobContext(
            job,
            store=self.store,
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            now=self._clock,
        )
        try:
            context.checkpoint(job.progress)
            if handler is None:
                context.log("error", "unregistered job type")
                return self._finish_failure(job, "unregistered job type", retry_at=None)
            context.log("info", "job started")
            result = handler(context, freeze_mapping(job.payload))
            normalized = _result(result)
            context.checkpoint(context.progress)
            context.log("info", "job completed")
            try:
                completed = self.store.complete_job(
                    job.job_id,
                    worker_id=self.config.worker_id,
                    result=normalized,
                    now=self._clock(),
                )
            except RuntimeError as exc:
                raise LeaseLost("job lease is invalid") from exc
            return _outcome(completed)
        except JobCancelled:
            return self._finish_failure(job, "job cancelled", retry_at=None)
        except RetryableJobError as exc:
            try:
                delay = retry_delay(self.config, job.attempts, exc.retry_after)
            except ValueError as validation_error:
                self._safe_report(job, validation_error)
                try:
                    context.log("error", "invalid retry request")
                except LeaseLost:
                    return _job_outcome(job, "lease_lost")
                return self._finish_failure(job, "invalid retry request", retry_at=None)
            try:
                context.log("warning", "job retry scheduled")
            except LeaseLost:
                return _job_outcome(job, "lease_lost")
            transition_now = self._clock()
            return self._finish_failure(
                job,
                exc.public_message,
                retry_at=transition_now + delay,
                now=transition_now,
            )
        except LeaseLost:
            return _job_outcome(job, "lease_lost")
        except Exception as exc:
            self._safe_report(job, exc)
            try:
                context.log("error", "job handler failed")
            except LeaseLost:
                return _job_outcome(job, "lease_lost")
            message = "invalid handler result" if isinstance(exc, HandlerContractError) else "job handler failed"
            return self._finish_failure(job, message, retry_at=None)

    def _finish_failure(
        self,
        job: JobLike,
        message: str,
        *,
        retry_at: int | None,
        now: int | None = None,
    ) -> RunOutcome:
        transition_now = self._clock() if now is None else now
        try:
            updated = self.store.fail_job(
                job.job_id,
                worker_id=self.config.worker_id,
                error=public_message(message, fallback="job handler failed"),
                retry_at=retry_at,
                now=transition_now,
            )
        except RuntimeError:
            return _job_outcome(job, "lease_lost")
        return _outcome(updated)

    def _safe_report(self, job: JobLike, error: BaseException) -> None:
        try:
            self._reporter(job, error)
        except Exception:
            return


def _result(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HandlerContractError("handler result must be an object or null")
    return dict(value)


def _outcome(job: JobLike) -> RunOutcome:
    status = job.status if job.status in {"succeeded", "retry_wait", "failed", "cancelled"} else "lease_lost"
    return _job_outcome(job, status)


def _job_outcome(job: JobLike, status: str) -> RunOutcome:
    return RunOutcome(status, job.job_id, job.job_type, job.attempts)
