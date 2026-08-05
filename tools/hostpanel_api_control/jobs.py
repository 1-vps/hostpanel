from __future__ import annotations

import sqlite3

from .model import (
    JOB_ID_RE,
    JOB_STATUSES,
    MAX_ATTEMPTS,
    MAX_JOB_LOGS,
    MAX_JOB_PAGE,
    MAX_LEASE_SECONDS,
    MAX_LOG_BYTES,
    ConflictError,
    Job,
    JobCreation,
    JobLog,
    StateError,
    ValidationError,
    canonical_json,
    decode_json,
    encode_random,
    identifier,
    integer,
    kind,
    now as normalize_now,
    optional_key,
    text,
)
from .schema import atomic


class JobMixin:
    def create_job(
        self,
        *,
        tenant_id: str,
        job_type: str,
        payload: dict,
        dedupe_key: str | None = None,
        priority: int = 50,
        max_attempts: int = 3,
        available_at: int | None = None,
        now: int | None = None,
    ) -> JobCreation:
        timestamp = normalize_now(now)
        available = timestamp if available_at is None else normalize_now(available_at)
        if available < timestamp:
            raise ValidationError("job availability cannot predate creation")
        tenant = identifier(tenant_id, "tenant ID")
        category = kind(job_type, "job type")
        dedupe = optional_key(dedupe_key)
        priority_value = integer(priority, "job priority", minimum=0, maximum=100)
        attempts_limit = integer(
            max_attempts,
            "maximum attempts",
            minimum=1,
            maximum=MAX_ATTEMPTS,
        )
        payload_json, payload_hash = canonical_json(payload, "job payload")
        job_id = encode_random("job_")
        with atomic(self.connection):
            try:
                self.connection.execute(
                    """
                    INSERT INTO hp_api_jobs(
                        job_id, tenant_id, job_type, dedupe_key, payload_json,
                        payload_hash, status, priority, created_at, available_at,
                        attempts, max_attempts, cancel_requested, progress
                    ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, 0, ?, 0, 0)
                    """,
                    (
                        job_id,
                        tenant,
                        category,
                        dedupe,
                        payload_json,
                        payload_hash,
                        priority_value,
                        timestamp,
                        available,
                        attempts_limit,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if dedupe is None:
                    raise
                row = self.connection.execute(
                    """
                    SELECT job_id, payload_hash FROM hp_api_jobs
                    WHERE tenant_id = ? AND job_type = ? AND dedupe_key = ?
                      AND status IN ('pending', 'running', 'retry_wait')
                    """,
                    (tenant, category, dedupe),
                ).fetchone()
                if row is None:
                    raise
                if bytes(row[1]) != payload_hash:
                    raise ConflictError(
                        "active job dedupe key was reused with different payload"
                    ) from exc
                return JobCreation(self._load_job(row[0]), False)
        return JobCreation(self._load_job(job_id), True)

    def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: int | None = None,
    ) -> Job | None:
        timestamp = normalize_now(now)
        worker = identifier(worker_id, "worker ID")
        lease = integer(
            lease_seconds,
            "lease duration",
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        )
        with atomic(self.connection):
            self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET status = CASE
                      WHEN cancel_requested = 1 THEN 'cancelled'
                      WHEN attempts < max_attempts THEN 'retry_wait'
                      ELSE 'failed'
                    END,
                    available_at = ?,
                    finished_at = CASE
                      WHEN cancel_requested = 1 OR attempts >= max_attempts THEN ?
                      ELSE NULL
                    END,
                    error_text = 'worker lease expired',
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE status = 'running' AND lease_expires_at < ?
                """,
                (timestamp, timestamp, timestamp),
            )
            self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET status = 'cancelled', finished_at = ?, available_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE cancel_requested = 1
                  AND status IN ('pending', 'retry_wait')
                """,
                (timestamp, timestamp),
            )
            row = self.connection.execute(
                """
                SELECT job_id FROM hp_api_jobs
                WHERE status IN ('pending', 'retry_wait')
                  AND cancel_requested = 0 AND available_at <= ?
                ORDER BY priority DESC, available_at, created_at, job_id
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                return None
            result = self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = COALESCE(started_at, ?), lease_owner = ?,
                    lease_expires_at = ?
                WHERE job_id = ? AND status IN ('pending', 'retry_wait')
                  AND cancel_requested = 0 AND available_at <= ?
                """,
                (timestamp, worker, timestamp + lease, row[0], timestamp),
            )
            if result.rowcount != 1:
                return None
            return self._load_job(row[0])

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: int,
        lease_seconds: int = 60,
        now: int | None = None,
    ) -> Job:
        timestamp = normalize_now(now)
        job = self._job_id(job_id)
        worker = identifier(worker_id, "worker ID")
        progress_value = integer(progress, "job progress", minimum=0, maximum=100)
        lease = integer(
            lease_seconds,
            "lease duration",
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        )
        with atomic(self.connection):
            result = self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET progress = ?, lease_expires_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at >= ? AND started_at <= ? AND progress <= ?
                """,
                (
                    progress_value,
                    timestamp + lease,
                    job,
                    worker,
                    timestamp,
                    timestamp,
                    progress_value,
                ),
            )
            if result.rowcount != 1:
                raise StateError("job lease is invalid or progress moved backwards")
            return self._load_job(job)

    def append_job_log(
        self,
        job_id: str,
        *,
        worker_id: str,
        level: str,
        message: str,
        now: int | None = None,
    ) -> JobLog:
        timestamp = normalize_now(now)
        job = self._job_id(job_id)
        worker = identifier(worker_id, "worker ID")
        if level not in {"debug", "info", "warning", "error"}:
            raise ValidationError("job log level is invalid")
        log_message = text(message, "job log message", maximum=MAX_LOG_BYTES)
        if len(log_message.encode("utf-8")) > MAX_LOG_BYTES:
            raise ValidationError("job log message is too large")
        with atomic(self.connection):
            lease = self.connection.execute(
                """
                SELECT 1 FROM hp_api_jobs
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at >= ? AND started_at <= ?
                """,
                (job, worker, timestamp, timestamp),
            ).fetchone()
            if lease is None:
                raise StateError("job lease is invalid")
            count = self.connection.execute(
                "SELECT count(*) FROM hp_api_job_logs WHERE job_id = ?",
                (job,),
            ).fetchone()[0]
            if count >= MAX_JOB_LOGS:
                raise StateError("job log limit reached")
            cursor = self.connection.execute(
                """
                INSERT INTO hp_api_job_logs(job_id, occurred_at, level, message)
                VALUES(?, ?, ?, ?)
                """,
                (job, timestamp, level, log_message),
            )
            return JobLog(cursor.lastrowid, job, timestamp, level, log_message)

    def complete_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: dict | None = None,
        now: int | None = None,
    ) -> Job:
        timestamp = normalize_now(now)
        job = self._job_id(job_id)
        worker = identifier(worker_id, "worker ID")
        result_json, _result_hash = canonical_json(result, "job result")
        with atomic(self.connection):
            updated = self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET status = CASE WHEN cancel_requested = 1 THEN 'cancelled' ELSE 'succeeded' END,
                    result_json = CASE WHEN cancel_requested = 1 THEN NULL ELSE ? END,
                    error_text = CASE WHEN cancel_requested = 1 THEN error_text ELSE NULL END,
                    finished_at = ?, progress = CASE WHEN cancel_requested = 1 THEN progress ELSE 100 END,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at >= ? AND started_at <= ?
                """,
                (result_json, timestamp, job, worker, timestamp, timestamp),
            )
            if updated.rowcount != 1:
                raise StateError("job lease is invalid")
            return self._load_job(job)

    def fail_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: int | None = None,
        now: int | None = None,
    ) -> Job:
        timestamp = normalize_now(now)
        job = self._job_id(job_id)
        worker = identifier(worker_id, "worker ID")
        error_text = text(error, "job error", maximum=4096)
        retry = None if retry_at is None else normalize_now(retry_at)
        if retry is not None and retry <= timestamp:
            raise ValidationError("retry time must be in the future")
        with atomic(self.connection):
            row = self.connection.execute(
                """
                SELECT attempts, max_attempts, cancel_requested
                FROM hp_api_jobs
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at >= ? AND started_at <= ?
                """,
                (job, worker, timestamp, timestamp),
            ).fetchone()
            if row is None:
                raise StateError("job lease is invalid")
            attempts, max_attempts, cancel_requested = row
            if cancel_requested:
                status, available, finished = "cancelled", timestamp, timestamp
            elif retry is not None and attempts < max_attempts:
                status, available, finished = "retry_wait", retry, None
            else:
                status, available, finished = "failed", timestamp, timestamp
            self.connection.execute(
                """
                UPDATE hp_api_jobs
                SET status = ?, error_text = ?, available_at = ?, finished_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE job_id = ?
                """,
                (status, error_text, available, finished, job),
            )
            return self._load_job(job)

    def request_job_cancel(
        self,
        job_id: str,
        *,
        now: int | None = None,
    ) -> Job:
        timestamp = normalize_now(now)
        job = self._job_id(job_id)
        with atomic(self.connection):
            row = self.connection.execute(
                "SELECT status, created_at FROM hp_api_jobs WHERE job_id = ?",
                (job,),
            ).fetchone()
            if row is None:
                raise StateError("job does not exist")
            if timestamp < row[1]:
                raise StateError("job transition cannot predate creation")
            if row[0] in {"succeeded", "failed", "cancelled"}:
                return self._load_job(job)
            if row[0] in {"pending", "retry_wait"}:
                self.connection.execute(
                    """
                    UPDATE hp_api_jobs
                    SET cancel_requested = 1, status = 'cancelled', finished_at = ?,
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE job_id = ?
                    """,
                    (timestamp, job),
                )
            else:
                self.connection.execute(
                    "UPDATE hp_api_jobs SET cancel_requested = 1 WHERE job_id = ?",
                    (job,),
                )
            return self._load_job(job)

    def list_jobs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        after: tuple[int, str] | None = None,
        limit: int = 100,
    ) -> tuple[Job, ...]:
        tenant = None if tenant_id is None else identifier(tenant_id, "tenant ID")
        if status is not None and status not in JOB_STATUSES:
            raise ValidationError("job status is invalid")
        page = integer(limit, "job page size", minimum=1, maximum=MAX_JOB_PAGE)
        if after is None:
            cursor_time, cursor_id = -1, ""
        else:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValidationError("job cursor is invalid")
            cursor_time = normalize_now(after[0])
            cursor_id = self._job_id(after[1])
        conditions = ["(created_at > ? OR (created_at = ? AND job_id > ?))"]
        arguments: list[object] = [cursor_time, cursor_time, cursor_id]
        if tenant is not None:
            conditions.append("tenant_id = ?")
            arguments.append(tenant)
        if status is not None:
            conditions.append("status = ?")
            arguments.append(status)
        arguments.append(page)
        rows = self.connection.execute(
            f"SELECT job_id FROM hp_api_jobs WHERE {' AND '.join(conditions)} ORDER BY created_at, job_id LIMIT ?",
            arguments,
        ).fetchall()
        return tuple(self._load_job(row[0]) for row in rows)

    def list_job_logs(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobLog, ...]:
        job = self._job_id(job_id)
        cursor = integer(
            after_sequence,
            "job log cursor",
            minimum=0,
            maximum=2**63 - 1,
        )
        page = integer(limit, "job log page size", minimum=1, maximum=1000)
        rows = self.connection.execute(
            """
            SELECT sequence, occurred_at, level, message
            FROM hp_api_job_logs
            WHERE job_id = ? AND sequence > ?
            ORDER BY sequence LIMIT ?
            """,
            (job, cursor, page),
        ).fetchall()
        return tuple(JobLog(seq, job, at, level, message) for seq, at, level, message in rows)

    def _job_id(self, value: str) -> str:
        job = identifier(value, "job ID")
        if JOB_ID_RE.fullmatch(job) is None:
            raise ValidationError("job ID is invalid")
        return job

    def _load_job(self, job_id: str) -> Job:
        row = self.connection.execute(
            """
            SELECT tenant_id, job_type, dedupe_key, status, payload_json,
                   result_json, error_text, priority, created_at, available_at,
                   started_at, finished_at, attempts, max_attempts,
                   cancel_requested, progress, lease_owner, lease_expires_at
            FROM hp_api_jobs WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise StateError("job does not exist")
        return Job(
            job_id,
            row[0],
            row[1],
            row[2],
            row[3],
            decode_json(row[4]) or {},
            decode_json(row[5]),
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
            row[12],
            row[13],
            bool(row[14]),
            row[15],
            row[16],
            row[17],
        )
