from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import sqlite3

from hostpanel_api_http import RateLimitDecision
from integration_test_models import *


class FakeControlStore:
    def __init__(self, connection: sqlite3.Connection, trace: list[str]) -> None:
        self.connection = connection
        self.trace = trace
        self.next_job = 1
        self.idempotency: dict[tuple[str, str], Any] = {}
        self.fail_after_job_insert = False

    def migrate(self):
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS hp_api_jobs("
            "job_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, job_type TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at INTEGER NOT NULL, cancel_requested INTEGER NOT NULL)"
        )

    def begin_idempotent_request(self, *, principal_id, key, http_method, request_route, request_body, expires_at, now=None):
        from types import SimpleNamespace
        map_key = (principal_id, key)
        fingerprint = (http_method, request_route, bytes(request_body))
        existing = self.idempotency.get(map_key)
        if existing is None:
            decision = SimpleNamespace(record_id=f"idem_{len(self.idempotency)+1}", created=True, state="pending", response_status=None, response_headers=None, response_body=None, fingerprint=fingerprint)
            self.idempotency[map_key] = decision
            return decision
        if existing.fingerprint != fingerprint:
            raise type("ConflictError", (RuntimeError,), {})("different request")
        return SimpleNamespace(record_id=existing.record_id, created=False, state=existing.state, response_status=existing.response_status, response_headers=existing.response_headers, response_body=existing.response_body)

    def complete_idempotent_request(self, record_id, *, response_status, response_body, response_headers_map=None, now=None):
        for decision in self.idempotency.values():
            if decision.record_id == record_id:
                decision.state = "completed"
                decision.response_status = response_status
                decision.response_headers = dict(response_headers_map or {})
                decision.response_body = bytes(response_body)
                return decision
        raise RuntimeError("missing record")

    def create_job(self, *, tenant_id, job_type, payload, dedupe_key=None, priority=50, max_attempts=3, available_at=None, now=None):
        job_id = f"job_test{self.next_job:017d}"
        self.next_job += 1
        self.connection.execute("INSERT INTO hp_api_jobs VALUES(?, ?, ?, 'pending', ?, 0)", (job_id, tenant_id, job_type, now))
        if self.fail_after_job_insert:
            raise type("StateError", (RuntimeError,), {})("forced after insert")
        return JobCreation(Job(job_id, tenant_id, job_type, dedupe_key, "pending", dict(payload), None, None, priority, now, now, None, None, 0, max_attempts, False, 0, None, None), True)

    def get_job(self, job_id):
        return self._load_job(job_id)

    def _load_job(self, job_id):
        row = self.connection.execute("SELECT tenant_id, job_type, status, created_at, cancel_requested FROM hp_api_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise type("StateError", (RuntimeError,), {})("job does not exist")
        return Job(job_id, row[0], row[1], None, row[2], {}, None, None, 50, row[3], row[3], None, None, 0, 3, bool(row[4]), 0, None, None)

    def list_jobs(self, *, tenant_id=None, status=None, after=None, limit=100):
        sql = "SELECT job_id FROM hp_api_jobs WHERE 1=1"
        args: list[Any] = []
        if tenant_id is not None:
            sql += " AND tenant_id = ?"; args.append(tenant_id)
        if status is not None:
            sql += " AND status = ?"; args.append(status)
        sql += " ORDER BY created_at, job_id LIMIT ?"; args.append(limit)
        return tuple(self._load_job(row[0]) for row in self.connection.execute(sql, args))

    def request_job_cancel(self, job_id, *, now=None):
        result = self.connection.execute("UPDATE hp_api_jobs SET cancel_requested = 1, status = 'cancelled' WHERE job_id = ?", (job_id,))
        if result.rowcount != 1:
            raise type("StateError", (RuntimeError,), {})("job does not exist")
        return self._load_job(job_id)

    def list_job_logs(self, job_id, *, after_sequence=0, limit=100):
        self._load_job(job_id)
        return tuple(JobLog(index, job_id, index, "info", f"log {index}") for index in range(after_sequence + 1, after_sequence + limit + 1))
