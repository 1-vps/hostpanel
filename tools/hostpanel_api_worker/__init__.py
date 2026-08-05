"""Fixed-registry worker runtime for HostPanel durable API jobs."""

from .compatibility import REQUIRED_STORE_METHODS, verify_store_contract
from .context import JobContext
from .model import (
    HandlerContractError,
    JobCancelled,
    LeaseLost,
    RegistryError,
    RetryableJobError,
    RunOutcome,
    WorkerConfig,
    WorkerError,
)
from .registry import Handler, HandlerRegistry
from .runner import WorkerRunner
from .service import WorkerService
from .store import JobStore

__all__ = (
    "Handler",
    "HandlerContractError",
    "HandlerRegistry",
    "JobCancelled",
    "JobContext",
    "JobStore",
    "LeaseLost",
    "RegistryError",
    "REQUIRED_STORE_METHODS",
    "RetryableJobError",
    "RunOutcome",
    "WorkerConfig",
    "WorkerError",
    "WorkerRunner",
    "WorkerService",
    "verify_store_contract",
)
