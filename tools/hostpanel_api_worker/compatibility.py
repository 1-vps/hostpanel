from __future__ import annotations

from .model import WorkerError

REQUIRED_STORE_METHODS = (
    "append_job_log",
    "claim_job",
    "complete_job",
    "fail_job",
    "heartbeat_job",
)


def verify_store_contract(store: object) -> None:
    missing = tuple(
        name for name in REQUIRED_STORE_METHODS if not callable(getattr(store, name, None))
    )
    if missing:
        raise WorkerError("job store does not implement the reviewed worker contract")
