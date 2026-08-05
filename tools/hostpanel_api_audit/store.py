from __future__ import annotations

import base64
import secrets
import sqlite3

from .append import AppendMixin
from .hashing import genesis_hash
from .export import ExportMixin
from .model import RetentionPolicy
from .query import QueryMixin
from .retention import RetentionMixin
from .schema import initialize_connection, migrate as migrate_schema, validate_schema_shape
from .validation import retention_policy
from .verify import VerifyMixin


class ApiAuditStore(AppendMixin, QueryMixin, ExportMixin, RetentionMixin, VerifyMixin):
    def __init__(self, connection: sqlite3.Connection, *, policy: RetentionPolicy | None = None):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self.connection = connection
        self.policy = retention_policy(policy or RetentionPolicy())
        initialize_connection(connection)

    def migrate(self) -> None:
        migrate_schema(self.connection)

    def verify_schema(self) -> None:
        validate_schema_shape(self.connection)

    @staticmethod
    def genesis_hash() -> bytes:
        return genesis_hash()

    @staticmethod
    def new_event_id() -> str:
        token = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode("ascii")
        return "aud_" + token
