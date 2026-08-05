from __future__ import annotations

import sqlite3

from .schema import migrate, verify_schema
from .store_admin import DestinationAdminMixin
from .store_read import DestinationReadMixin


class WebhookDestinationStore(DestinationAdminMixin, DestinationReadMixin):
    sqlite_integrity_error = sqlite3.IntegrityError

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def migrate(self) -> None:
        migrate(self.connection)

    def verify_schema(self) -> None:
        verify_schema(self.connection)
