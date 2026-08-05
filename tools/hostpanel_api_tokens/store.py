from __future__ import annotations

from .accounts import AccountMixin
from .base import StoreBase
from .tokens import TokenMixin


class ApiTokenStore(TokenMixin, AccountMixin, StoreBase):
    """Transactional service-account and API-token store."""
