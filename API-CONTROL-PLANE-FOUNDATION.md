# HostPanel API control-plane foundation

This document defines the second implementation slice of product-parity epic
#143. It builds on the service-account and API-token policy in PR #152 and adds
transactional idempotency, durable jobs, a delivery outbox, and webhook
signature/replay primitives.

It does not expose HTTP routes, start workers, or configure webhook destinations.
Those integrations remain separate reviewed changes against the signed
application.

## Delivered contract

The `hostpanel_api_control` package accepts a caller-owned `sqlite3.Connection`.
It creates only `hp_api_*` runtime objects, disables SQLite trusted schema,
enables foreign-key enforcement, and uses nested savepoints so the caller may
commit or roll back business data and control-plane state together.

### Idempotent mutations

- principal-scoped idempotency keys with method, route, and body fingerprints;
- insert-first conflict resolution so concurrent callers produce one creator;
- pending replay and immutable completed response replay;
- bounded TTL, request/response bodies, status codes, and canonical headers;
- stable response bytes and headers for every valid replay; and
- bounded expiry pruning.

The endpoint integration must create the idempotency record and business change
inside the same outer transaction. A pending record is not permission to replay a
partial side effect; the adapter must resume or report the in-progress operation
according to the endpoint contract.

### Durable background jobs

- tenant, type, payload, priority, availability, attempts, and optional active
  deduplication;
- single-winner claims with bounded worker leases;
- monotonic progress, bounded logs, cancellation, retry scheduling, and results;
- automatic recovery of expired leases with an explicit current failure reason;
- bounded stable job and log pagination; and
- database constraints for terminal status, timestamps, attempts, results,
  errors, and leases.

Workers must use dedicated unprivileged processes. A job payload is data, never a
shell command. The job handler registry must map reviewed job types to fixed code
and explicit capability checks.

### Transactional outbox and webhooks

- durable events with destination, type, canonical payload, attempts, leases,
  retry/failure state, and a permanent destination-scoped dedupe key;
- single-winner delivery claims and expired-lease recovery;
- HMAC-SHA256 signatures over protocol version, timestamp, event ID, and payload
  digest;
- strict signature syntax, constant-time comparison, bounded clock tolerance,
  and per-receiver replay receipts; and
- receipt expiry permitting a newly signed redelivery after the replay window.

A dedupe key identifies one logical event permanently for a destination. Reusing
it with a different type or payload is a conflict, including after successful
delivery. Secret rotation, destination policy, HTTP transport, redirects,
certificate validation, response classification, and retry backoff belong to the
later webhook-delivery adapter.

## Security invariants

- All persisted JSON is a bounded, canonical object with string keys, finite
  numbers, bounded depth, bounded node count, and signed-64-bit integers.
- Persisted response headers and JSON are revalidated on read and fail closed if
  altered into an unreviewed shape.
- Creation/scheduling, completion, delivery, cancellation, and lease operations
  cannot move backwards before durable creation/start timestamps.
- Claims are serialized by SQLite writes and cross-connection tests require one
  winner for jobs and outbox events.
- Idempotency creation is concurrency-safe and returns one creator plus matching
  pending replays.
- Attempts cannot exceed their configured maximum, terminal rows require terminal
  timestamps, successful jobs require a result and 100% progress, and delivered
  events require a delivery timestamp with no error.
- Job and event payloads are never evaluated or interpolated into commands.
- Package methods preserve a caller-owned outer transaction.

## Required signed-application integration

The application adapter must:

1. authenticate and authorize through the token/RBAC layer before creating any
   idempotency, job, or outbox state;
2. derive tenant and resource ownership from authorized database objects rather
   than caller-supplied identifiers alone;
3. write business state, idempotency completion, audit, jobs, and outbox events in
   one outer transaction where the operation requires them;
4. map reviewed fixed job handlers rather than accepting executable input;
5. use bounded log redaction so credentials, Authorization headers, private keys,
   and secret-bearing URLs cannot enter job logs or webhook payloads;
6. implement explicit HTTP response classification and bounded exponential retry
   with jitter outside the persistence package;
7. expose stable request IDs, job IDs, event IDs, pagination cursors, and JSON
   errors; and
8. add endpoint-by-endpoint tenant isolation, object authorization, cancellation,
   retry, duplicate request, process restart, and reboot tests.

## Not yet delivered

This slice does not complete #143. Remaining work includes:

- `/api/v1` routes and generated OpenAPI;
- HTTP bearer parsing, rate limits, request IDs, and JSON error envelopes;
- worker service installation, handler registry, resource limits, and lifecycle;
- webhook destination/secret management and real HTTP delivery;
- interactive-user capability RBAC, policy simulation, and audited impersonation;
- service-account/token/job/webhook management UI and CLI; and
- production VM and external integration acceptance.

No dependent parity epic may treat these primitives alone as a complete API or
webhook product.
