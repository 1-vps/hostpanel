from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from hostpanel_api_audit import ApiAuditStore
from hostpanel_api_control import ApiControlStore
from hostpanel_api_http import Request, SqliteRateLimiter
from hostpanel_api_integration import ApiCli, ManagementApi, StoreBundle
from hostpanel_api_rbac import ApiRbacStore, AuditActor
from hostpanel_api_tokens import Actor, ApiTokenStore
from hostpanel_api_webhooks import WebhookDestinationStore

scopes = (
    'audit:export', 'audit:read', 'job:cancel', 'job:create', 'job:read',
    'policy:simulate', 'service_account:create', 'service_account:read',
    'service_account:update', 'token:issue', 'token:read', 'token:revoke',
    'webhook:create', 'webhook:disable', 'webhook:read', 'webhook:update',
)
now = 1_000
with tempfile.TemporaryDirectory() as directory:
    database = str(Path(directory) / 'stack.sqlite3')
    connection = sqlite3.connect(database)
    rate_connection = sqlite3.connect(database)
    try:
        stores = StoreBundle(
            ApiTokenStore(connection),
            ApiControlStore(connection),
            ApiRbacStore(connection),
            WebhookDestinationStore(connection),
            ApiAuditStore(connection),
        )
        stores.migrate_all()
        stores.verify_compatibility()

        token_actor = Actor('system', 'bootstrap')
        rbac_actor = AuditActor('system', 'bootstrap')
        account = stores.tokens.create_service_account(
            name='integration administrator',
            scopes=scopes,
            tenant_ids=('tenant-a',),
            actor=token_actor,
            now=now,
        )
        stores.rbac.create_principal(
            principal_id=account.account_id,
            principal_kind='service_account',
            actor=rbac_actor,
            now=now,
        )
        role = stores.rbac.create_role(
            name='integration administrator',
            allows=scopes,
            actor=rbac_actor,
            now=now,
        )
        stores.rbac.bind_role(
            principal_id=account.account_id,
            role_id=role.role_id,
            tenant_ids=('tenant-a',),
            allow_all_resources=True,
            actor=rbac_actor,
            now=now,
        )
        issued = stores.tokens.issue_token(
            account.account_id,
            expires_at=now + 86_400,
            actor=token_actor,
            now=now,
        )
        identity = stores.tokens.authenticate_identity(
            issued.token,
            source_ip='203.0.113.10',
            now=now + 1,
        )
        assert identity.account_id == account.account_id
        assert stores.tokens.get_service_account(account.account_id).account_id == account.account_id
        assert stores.tokens.list_service_accounts(tenant_id='tenant-a')[0].account_id == account.account_id

        limiter = SqliteRateLimiter(rate_connection, key_secret=b'r' * 32)
        limiter.migrate()
        app = ManagementApi(
            stores=stores,
            tenant_exists=lambda tenant: tenant == 'tenant-a',
            cursor_secret=b'c' * 32,
            rate_limiter=limiter,
            allowed_job_types={'site.backup'},
        )

        def call(method, path, *, body=None, key=None, at=now + 10, query=''):
            headers = [
                ('Authorization', 'Bearer ' + issued.token),
                ('X-Request-Id', f'request-{at:08d}'),
            ]
            raw = b''
            if body is not None:
                raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
                headers.append(('Content-Type', 'application/json'))
            if key is not None:
                headers.append(('Idempotency-Key', key))
            response = app.dispatch(Request(
                method=method,
                raw_path=path,
                query_string=query,
                headers=tuple(headers),
                body=raw,
                source_ip='203.0.113.10',
                scheme='https',
                host='panel.example',
            ), now=at)
            payload = json.loads(response.body) if response.body else None
            return response, payload

        health = app.dispatch(Request('GET', '/api/v1/health'), now=now + 2)
        assert health.status == 200
        openapi = app.dispatch(Request('GET', '/api/v1/openapi.json'), now=now + 2)
        contract = json.loads(openapi.body)
        parameters = contract['paths']['/api/v1/tenants/{tenant_id}/jobs']['get']['parameters']
        assert {'cursor', 'limit', 'status'} <= {item.get('name') for item in parameters if isinstance(item, dict)}

        create_job_request = dict(
            method='POST', path='/api/v1/tenants/tenant-a/jobs',
            body={'job_type': 'site.backup', 'payload': {'site_id': 'site-1'}},
            key='integration-job-000001', at=now + 10,
        )
        first, first_payload = call(**create_job_request)
        replay, replay_payload = call(**{**create_job_request, 'at': now + 11})
        assert first.status == replay.status == 201
        assert first_payload == replay_payload
        assert ('Idempotency-Replayed', 'true') in replay.headers
        job_id = first_payload['job']['job_id']
        assert stores.control.get_job(job_id).tenant_id == 'tenant-a'

        created_account, account_payload = call(
            'POST', '/api/v1/tenants/tenant-a/service-accounts',
            body={'name': 'backup robot', 'scopes': ['job:read']},
            key='integration-account-01', at=now + 20,
        )
        assert created_account.status == 201
        child_account = account_payload['service_account']['account_id']

        issue_request = dict(
            method='POST',
            path=f'/api/v1/tenants/tenant-a/service-accounts/{child_account}/tokens',
            body={'expires_at': now + 3_600},
            key='integration-token-0001',
            at=now + 21,
        )
        issued_once, issued_payload = call(**issue_request)
        issued_replay, replay_payload = call(**{**issue_request, 'at': now + 22})
        assert issued_once.status == issued_replay.status == 201
        assert issued_payload['secret_available'] is True and 'token' in issued_payload
        assert replay_payload['secret_available'] is False and 'token' not in replay_payload
        persisted = connection.execute(
            "SELECT response_body FROM hp_api_idempotency WHERE route LIKE '%/tokens'"
        ).fetchone()[0]
        assert b'hpv1.' not in bytes(persisted)

        webhook, webhook_payload = call(
            'POST', '/api/v1/tenants/tenant-a/webhook-destinations',
            body={
                'url': 'https://hooks.example/path',
                'secret_reference': 'vault:webhook/primary',
                'event_types': ['job.succeeded'],
            },
            key='integration-webhook-01', at=now + 30,
        )
        assert webhook.status == 201
        assert 'secret_reference' not in webhook_payload['destination']
        assert 'secret_ref' not in webhook_payload['destination']
        assert webhook_payload['destination']['secret_configured'] is True

        audit_response, audit_payload = call(
            'GET', '/api/v1/tenants/tenant-a/audit-events',
            query='limit=50', at=now + 40,
        )
        assert audit_response.status == 200 and audit_payload['events']
        export_response, export_payload = call(
            'POST', '/api/v1/tenants/tenant-a/audit-exports',
            body={'format': 'jsonl', 'limit': 50},
            key='integration-audit-0001', at=now + 41,
        )
        assert export_response.status == 201
        assert len(export_payload['sha256']) == 64
        verification = stores.audit.verify_chain()
        assert verification.event_count >= 1
        assert verification.last_sequence == verification.event_count

        cli = ApiCli(app)
        cli_result = cli.execute([
            'GET', '/api/v1/tenants/tenant-a/jobs',
            '--token', issued.token,
            '--request-id', 'request-cli-0001',
        ], now=now + 50)
        assert cli_result.exit_code == 0 and job_id in cli_result.stdout
    finally:
        connection.close()
        rate_connection.close()

# New-database migration remains rollback-safe under a caller transaction.
connection = sqlite3.connect(':memory:')
try:
    rollback_bundle = StoreBundle(
        ApiTokenStore(connection), ApiControlStore(connection),
        ApiRbacStore(connection), WebhookDestinationStore(connection),
        ApiAuditStore(connection),
    )
    connection.execute('BEGIN')
    rollback_bundle.migrate_all()
    connection.rollback()
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='hp_api_jobs'"
    ).fetchone() is None
finally:
    connection.close()
