from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpanel_api_http.model import RequestContext

from .stores import StoreBundle


class AuditWriter:
    def __init__(self, stores: StoreBundle) -> None:
        self.stores = stores

    def append(
        self,
        context: RequestContext,
        *,
        action: str,
        outcome: str = "succeeded",
        target_kind: str | None = None,
        target_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        reason_code: str | None = None,
        retention_class: str = "standard",
    ):
        if context.principal is None or context.target is None:
            raise RuntimeError("central audit requires an authenticated target")
        return self.stores.audit.append_event(
            tenant_id=context.target.tenant_id,
            actor_kind="service_account",
            actor_id=context.principal.principal_id,
            action=action,
            outcome=outcome,
            target_kind=target_kind,
            target_id=target_id,
            request_id=context.request_id,
            source_ip=context.request.source_ip,
            reason_code=reason_code,
            metadata=metadata,
            retention_class=retention_class,
            occurred_at=context.now,
        )
