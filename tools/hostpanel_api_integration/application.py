from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from hostpanel_api_http import ApiApplication, CursorCodec

from .access import StackAccessController
from .auditing import AuditWriter
from .idempotency import IdempotentMutations
from .routes_audit import AuditRouteHandlers, register_audit_routes
from .routes_jobs import JobRouteHandlers, register_job_routes
from .routes_policy import PolicyRouteHandlers, register_policy_routes
from .routes_tokens import TokenRouteHandlers, register_token_routes
from .routes_webhooks import WebhookRouteHandlers, register_webhook_routes
from .stores import StoreBundle
from .targets import TrustedResolvers

_JOB_TYPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


class ManagementApi(
    TokenRouteHandlers,
    JobRouteHandlers,
    WebhookRouteHandlers,
    AuditRouteHandlers,
    PolicyRouteHandlers,
    ApiApplication,
):
    """Concrete /api/v1 management surface over the reviewed foundations."""

    def __init__(
        self,
        *,
        stores: StoreBundle,
        tenant_exists: Callable[[str], bool],
        cursor_secret: bytes,
        rate_limiter: Any,
        allowed_job_types: Iterable[str],
        error_reporter=None,
        title: str = "HostPanel Management API",
        version: str = "1.0.0",
    ) -> None:
        if not isinstance(stores, StoreBundle):
            raise TypeError("stores must be StoreBundle")
        job_types = frozenset(allowed_job_types)
        if not job_types or any(not isinstance(value, str) or _JOB_TYPE_RE.fullmatch(value) is None for value in job_types):
            raise ValueError("allowed job types are invalid")
        if rate_limiter is None or not callable(getattr(rate_limiter, "consume", None)):
            raise TypeError("rate_limiter must implement consume()")
        self.stores = stores
        self.allowed_job_types = job_types
        self.cursor_codec = CursorCodec(cursor_secret)
        self.resolvers = TrustedResolvers(stores, tenant_exists=tenant_exists)
        self.idempotency = IdempotentMutations(stores)
        self.audit_writer = AuditWriter(stores)
        super().__init__(
            access_controller=StackAccessController(stores),
            rate_limiter=rate_limiter,
            error_reporter=error_reporter,
            title=title,
            version=version,
        )
        register_token_routes(self)
        register_job_routes(self)
        register_webhook_routes(self)
        register_audit_routes(self)
        register_policy_routes(self)
        self.freeze()
