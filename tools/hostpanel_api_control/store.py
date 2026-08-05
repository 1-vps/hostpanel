from __future__ import annotations

import sqlite3

from .idempotency import IdempotencyMixin
from .jobs import JobMixin
from .outbox import OutboxMixin
from .schema import initialize_connection, migrate as migrate_schema


class ApiControlStore(IdempotencyMixin, JobMixin, OutboxMixin):
    """Transactional idempotency, job, and outbox persistence."""

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self.connection = connection
        initialize_connection(connection)

    def migrate(self) -> None:
        migrate_schema(self.connection)
