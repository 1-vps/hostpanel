from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .model import RunOutcome
from .runner import WorkerRunner


class StopSignal(Protocol):
    def is_set(self) -> bool: ...


class WorkerService:
    __slots__ = ("runner", "_sleep")

    def __init__(self, runner: WorkerRunner, *, sleep: Callable[[float], None]) -> None:
        if not callable(sleep):
            raise ValueError("sleep must be callable")
        self.runner = runner
        self._sleep = sleep

    def run_cycle(self, *, max_jobs: int | None = None) -> tuple[RunOutcome, ...]:
        limit = self.runner.config.max_jobs_per_cycle if max_jobs is None else max_jobs
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("max_jobs is invalid")
        outcomes: list[RunOutcome] = []
        for _ in range(limit):
            outcome = self.runner.run_once()
            outcomes.append(outcome)
            if outcome.status == "idle":
                break
        return tuple(outcomes)

    def run_until_stopped(self, stop: StopSignal) -> None:
        while not stop.is_set():
            outcomes = self.run_cycle()
            if outcomes and outcomes[-1].status == "idle" and not stop.is_set():
                self._sleep(float(self.runner.config.idle_sleep_seconds))
