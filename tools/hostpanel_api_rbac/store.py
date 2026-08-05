from __future__ import annotations

from .admin import AdminMixin
from .base import StoreBase
from .impersonation import ImpersonationMixin
from .policy import PolicyMixin


class ApiRbacStore(ImpersonationMixin, PolicyMixin, AdminMixin, StoreBase):
    """Transactional HostPanel capability and impersonation policy store."""
