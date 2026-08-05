from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .model import RegistryError, validate_job_type

if TYPE_CHECKING:
    from .context import JobContext

Handler = Callable[["JobContext", Mapping[str, Any]], Mapping[str, Any] | None]


class HandlerRegistry:
    """Immutable job-type to callable mapping with no dynamic loading surface."""

    __slots__ = ("_handlers",)

    def __init__(self, handlers: Mapping[str, Handler] | Iterable[tuple[str, Handler]]):
        pairs = handlers.items() if isinstance(handlers, Mapping) else handlers
        reviewed: dict[str, Handler] = {}
        for job_type, handler in pairs:
            category = validate_job_type(job_type)
            if category in reviewed:
                raise RegistryError("duplicate job type")
            if not callable(handler):
                raise RegistryError("job handler must be callable")
            reviewed[category] = handler
        if not reviewed:
            raise RegistryError("handler registry cannot be empty")
        self._handlers = MappingProxyType(reviewed)

    @property
    def job_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def resolve(self, job_type: str) -> Handler | None:
        category = validate_job_type(job_type)
        return self._handlers.get(category)

    def __contains__(self, job_type: object) -> bool:
        return isinstance(job_type, str) and job_type in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)
