# HostPanel versioned API transport foundation

This document defines the fourth implementation slice of product-parity epic
#143. It builds on the service-account/token foundation in PR #152, durable
control-plane primitives in PR #153, and capability RBAC/impersonation in PR
#154.

The slice provides a framework-independent `/api/v1` transport contract and a
reproducible OpenAPI document. It does not mount routes into the signed HostPanel
application, listen on a network socket, or expose domain mutation endpoints.

## Delivered contract

The `hostpanel_api_http` package provides:

- a frozen, versioned route registry rooted strictly below `/api/v1/`;
- public health and generated OpenAPI endpoints;
- one route table as the source of truth for dispatch and OpenAPI generation;
- strict bearer-header parsing and a two-phase access-controller adapter;
- accepted or generated request IDs on every JSON response;
- stable JSON error envelopes with redacted internal failures;
- canonical object-only JSON request and response encoding with bounded size,
  depth, node count, string size, and signed-64-bit integers;
- strict path, query, header, media-type, content-length, and body handling;
- required, bounded `Idempotency-Key` parsing for reviewed mutation routes;
- HMAC-signed cursors bound to operation, principal, tenant, and expiry;
- bounded cursor/limit query parsing;
- fixed-window source and principal rate-limit primitives backed by SQLite;
- HMAC-hashed rate-limit bucket identities and bounded pruning; and
- a WSGI callable that never opens a socket or starts a server.

The OpenAPI contract is generated deterministically on demand from the built-in
route registry. The exact-head workflow generates it twice, compares the bytes, and validates
the resulting JSON document.

## Security invariants

- Protected routes require an exact scope and a trusted target resolver.
- The access controller owns the order: authenticate the credential, resolve the
  trusted tenant/resource target, then authorize that target.
- Path or query identifiers alone are not proof of ownership.
- Bearer authentication is separate from interactive sessions and impersonation.
- Duplicate security-sensitive headers fail closed.
- Encoded slashes, backslashes, traversal aliases, duplicate separators, malformed
  percent escapes, control characters, and trailing-slash aliases are rejected.
- Request and response JSON is canonical and handlers cannot send arbitrary raw
  JSON, set cookies, or override transport security headers.
- Callback exceptions and invalid public error details are rendered as a generic
  `internal_error`; detailed exceptions are available only to the configured
  out-of-band reporter.
- `REMOTE_ADDR` is authoritative for this adapter. Forwarded-client headers are
  not trusted by the package.
- `Transfer-Encoding` is rejected; a trusted front end must deliver a bounded,
  dechunked request with an exact body length.
- Rate-limit identities are not stored in clear text. A deployment-secret HMAC
  derives the durable bucket key.
- Rate-limit schema shape is checked exactly, writes are serialized before reads,
  and caller-owned outer transactions remain intact.

## Required signed-application integration

The later signed-application adapter must:

1. mount the WSGI callable only behind HostPanel's reviewed TLS/reverse-proxy
   boundary and pass the trusted peer address as `REMOTE_ADDR`;
2. configure concrete access-controller callbacks backed by PR #152 token policy
   and PR #154 capability policy;
3. derive tenant and resource ownership from trusted database rows, never from a
   path, query, or JSON identifier alone;
4. translate reviewed endpoint capabilities to exact token scopes and RBAC
   capabilities without wildcard expansion;
5. bind required idempotency keys to principal, method, route, and canonical body
   using PR #153 inside the same outer transaction as each mutation;
6. use a dedicated SQLite connection/database lifecycle for rate limiting and
   commit each accepted or rejected consumption independently of business data;
7. register domain endpoints and schemas explicitly so generated OpenAPI and
   dispatch remain identical;
8. attach audit events, request IDs, jobs, and outbox records transactionally
   where each endpoint contract requires them; and
9. add endpoint-by-endpoint tenant isolation, object authorization, retry,
   concurrency, localization, accessibility, reboot, and production-VM tests.

## Not yet delivered

This slice does not complete #143. Remaining work includes:

- mounting the transport into the signed HostPanel application;
- concrete token, interactive-user, and impersonation adapters;
- real sites, DNS, mail, database, backup, job, webhook, token, RBAC, and audit
  endpoints and their domain schemas;
- durable idempotency integration for each mutation;
- worker service installation and a fixed handler registry;
- webhook destination/secret management and real HTTP delivery;
- searchable/exportable audit UI and API;
- management UI and CLI parity;
- organization/reseller hierarchy; and
- production VM and external integration acceptance.

No dependent parity epic may treat this transport package or its two public
system endpoints as a complete HostPanel API product.
