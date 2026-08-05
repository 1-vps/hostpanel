from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_rbac import (
    ApiRbacStore,
    AuditActor,
    AuthorizationError,
    ConflictError,
    StateError,
    ValidationError,
)

NOW = 1_800_000_000
AUDITOR = AuditActor("system", "rbac-test-suite")
SUPPORT_ACTOR = AuditActor("operator", "support-a")


class RbacTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.store = ApiRbacStore(self.connection)
        self.store.migrate()

    def tearDown(self) -> None:
        self.connection.close()

    def principal(self, principal_id: str, kind: str = "operator", **overrides):
        values = {
            "principal_id": principal_id,
            "principal_kind": kind,
            "actor": AUDITOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.create_principal(**values)

    def role(self, name: str, *, allows=(), denies=(), **overrides):
        values = {
            "name": name,
            "allows": allows,
            "denies": denies,
            "actor": AUDITOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.create_role(**values)

    def bind(self, principal_id: str, role_id: str, **overrides):
        values = {
            "principal_id": principal_id,
            "role_id": role_id,
            "tenant_ids": ("tenant-a",),
            "allow_all_resources": True,
            "actor": AUDITOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.bind_role(**values)
