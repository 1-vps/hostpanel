# HostPanel API token foundation

This document defines the first implementation slice of product-parity epic #143.
It provides a transactional service-account and API-token policy layer. It does
not expose HTTP routes and must not be described as a complete `/api/v1` API.

## Delivered contract

The `hostpanel_api_tokens` package accepts a caller-owned `sqlite3.Connection`.
It creates only `hp_api_*` objects and preserves an existing outer transaction by
using nested savepoints. Callers must invoke `ApiTokenStore.migrate()` before use.

The store supports:

- scoped service accounts with explicit tenants or an explicit all-tenant grant;
- optional source CIDR restrictions and account expiry;
- one-time token disclosure with mandatory expiry and optional narrower scopes,
  tenants, CIDRs, and per-resource grants;
- token revocation, account disablement, and generation-based emergency global
  revocation;
- authentication with scope, tenant, source-address, and resource checks;
- bounded stable pagination for token metadata and audit events; and
- append-only lifecycle audit records published in the same transaction as the
  state change they describe.

## Security invariants

- Token secrets use 32 random bytes and are returned only at issuance.
- SQLite stores a domain-separated SHA-256 verifier, never the token secret.
- Authentication failures for missing, malformed, unknown, expired, revoked,
  disabled, generation-stale, or source-rejected credentials use the same public
  `invalid API token` error.
- A token cannot expand its parent account's scopes, tenants, source networks, or
  expiry.
- `PRAGMA foreign_keys` must be enabled and `PRAGMA trusted_schema` must be off.
- Every reviewed table, index, and trigger must match the expected SQL shape.
- Audit rows cannot be updated or deleted through SQLite.
- Grant counts, text sizes, token TTL, result pages, and audit metadata are bounded.

## Required integration boundary

The signed application integration must remain a separate reviewed change after
PR #151 produces exact-head inventory evidence. That integration must:

1. reuse the application's existing database connection and transaction owner;
2. map every endpoint to an explicit `resource:action` scope;
3. derive tenant and resource identifiers from the authorized database object,
   never solely from caller input;
4. pass the trusted proxy-normalized source address, not an untrusted forwarding
   header, to `authenticate()`;
5. translate authentication failures to HTTP 401 and authorization failures to
   HTTP 403 without exposing credential state;
6. add request IDs, rate limits, stable JSON errors, idempotency, OpenAPI, and
   endpoint-by-endpoint object-authorization tests; and
7. retain session authentication and API-token authentication as separate,
   explicit mechanisms until policy parity is proven.

## Not yet delivered

The following #143 requirements remain separate implementation slices:

- `/api/v1` routes and OpenAPI generation;
- HTTP bearer parsing, request IDs, pagination envelopes, and rate limiting;
- idempotency storage;
- durable background jobs;
- transactional outbox and signed webhooks;
- capability RBAC for interactive users;
- policy simulation and audited impersonation; and
- management UI/CLI for service accounts and tokens.

No dependent parity epic may treat this persistence package alone as completion of
#143.
