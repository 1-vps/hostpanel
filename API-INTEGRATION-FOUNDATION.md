# HostPanel API integration foundation

This slice composes the previously reviewed token, control-plane, RBAC, HTTP,
worker, webhook, and audit foundations into one concrete `/api/v1` management
surface and one CLI adapter. It is stacked on PR #158 and does not modify the
signed HostPanel application archive.

## Delivered management surface

The route registry now exposes tenant-bound operations for:

- service-account creation, listing, reading, enable/disable, token issuance,
  token metadata listing, and token revocation;
- durable job creation, listing, reading, cancellation, and bounded logs;
- webhook destination creation, reading, replacement, and disablement;
- exact RBAC policy simulation;
- audit search, event reading, and bounded JSONL/CSV export.

The exact same frozen route table drives HTTP dispatch, generated OpenAPI, and
the CLI adapter. Query parameters are declared on routes and emitted by the
OpenAPI generator rather than being maintained separately.

## Authorization and tenant isolation

Protected requests execute in this order inside one caller-owned SQLite
savepoint:

1. cryptographically authenticate the bearer credential and account state;
2. resolve the requested tenant/resource from trusted persistent state;
3. enforce token scope, tenant, source-CIDR, and optional resource grants;
4. enforce the matching RBAC capability for the same trusted target.

Missing or cross-tenant objects are returned as `404`; invalid credentials as
`401`; and valid credentials lacking token or RBAC permission as `403`.
Service-account creation atomically creates the credential account, matching
RBAC principal, exact-capability role, tenant binding, and central audit event.

## Atomic mutation contract

Every management mutation uses a shared SQLite connection and an outer
savepoint. Idempotency state, domain state, RBAC state, and central audit state
therefore commit or roll back together. Stable canonical JSON responses are
persisted for normal idempotent replay.

Token issuance has a stricter contract: the first successful response contains
the one-time token secret, while the persisted/replayed response contains only
metadata and `secret_available: false`. Raw token plaintext is never written to
the idempotency response body.

Webhook responses omit the secret-reference identifier and expose only
`secret_configured: true`. Central audit metadata records that a reference was
changed without recording the reference value.

## Foundation contract additions

The token store now exposes:

- `authenticate_identity()` for the pre-target credential phase;
- `get_service_account()`;
- `list_service_accounts()` with tenant filtering and a stable cursor.

The control-plane store now exposes `get_job()`. The integration layer no
longer relies on private account or job loaders.

The HTTP route contract now carries the exact dispatch timestamp in
`RequestContext` and supports reviewed query-parameter declarations that are
included in generated OpenAPI.

## Verification

Local verification includes compilation plus integration regressions covering:

- authentication/target/token/RBAC evaluation order;
- endpoint-level tenant isolation and hidden cross-tenant objects;
- atomic service-account and RBAC provisioning rollback;
- job mutation idempotency, conflict handling, and replay;
- one-time token disclosure without plaintext persistence;
- job, webhook, audit, export, pagination, and cursor behavior;
- CLI and HTTP parity through one route table;
- schema/migration rollback and same-connection enforcement;
- forbidden dynamic loading, process execution, socket/server, and eval surfaces.

The exact-head workflow additionally instantiates the real stores from PRs
#152–#158 against one SQLite database, runs the concrete API and CLI, verifies
OpenAPI, central audit-chain integrity, one-time-secret persistence rules, and
new-database migration rollback.

## Deliberate remaining boundaries

This slice does not claim completion of the external/product gates that cannot
be validated in this repository state:

- GitHub-hosted jobs cannot execute while billing/spending blocker #13 remains;
- the signed HostPanel runtime cannot be mounted safely until the signed-source
  inventory and archive workflow in #151 execute successfully;
- no systemd unit, network listener, DNS-pinning production HTTP client, secret
  manager, or production handler registry is installed here;
- UI accessibility/localization, signed UI adapters, reboot persistence, and
  production VM acceptance require the signed runtime and functioning runners;
- external audit-chain anchoring, legal-hold/archive deletion lifecycle, and
  emergency global-token-revoke operator UX remain deployment integrations.

Keep the PR as draft and preserve stack order until every exact-head workflow
actually runs and the signed-runtime/VM gates are completed.
