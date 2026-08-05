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

from hostpanel_api_control import (
    ApiControlStore,
    AuthenticationError,
    ConflictError,
    ReplayError,
    StateError,
    ValidationError,
    sign_webhook,
    verify_webhook_signature,
)

NOW = 1_800_000_000


class ApiControlTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.store = ApiControlStore(self.connection)
        self.store.migrate()

    def tearDown(self) -> None:
        self.connection.close()

    def job(self, **overrides):
        values = {
            "tenant_id": "tenant-a",
            "job_type": "site.deploy",
            "payload": {"site": "example.com"},
            "now": NOW,
        }
        values.update(overrides)
        return self.store.create_job(**values)

    def outbox(self, **overrides):
        values = {
            "destination_id": "webhook-primary",
            "event_type": "site.created",
            "payload": {"site": "example.com"},
            "now": NOW,
        }
        values.update(overrides)
        return self.store.enqueue_outbox_event(**values)
