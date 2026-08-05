from __future__ import annotations

from .accounts import AccountMixin
from .base import StoreBase
from .model import Principal
from .schema import atomic
from .tokens import TokenMixin


class ApiTokenStore(TokenMixin, AccountMixin, StoreBase):
    """Transactional service-account and API-token store."""

    def authenticate(
        self,
        raw_token: str,
        *,
        required_scope: str,
        tenant_id: str,
        source_ip: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        now: int | None = None,
        touch: bool = True,
    ) -> Principal:
        with atomic(self.connection):
            return super().authenticate(
                raw_token,
                required_scope=required_scope,
                tenant_id=tenant_id,
                source_ip=source_ip,
                resource_type=resource_type,
                resource_id=resource_id,
                now=now,
                touch=touch,
            )
