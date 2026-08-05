# HostPanel capability RBAC foundation

This document defines the third implementation slice of product-parity epic #143.
It is stacked on the service-account/token foundation in PR #152 and the durable
control-plane foundation in PR #153.

The slice provides capability policy, simulation, and audited impersonation
primitives. It does not expose HTTP routes, replace the signed application's
session authorization, or add management UI/CLI.

## Delivered contract

The `hostpanel_api_rbac` package accepts a caller-owned `sqlite3.Connection`,
enables foreign-key enforcement, disables trusted SQLite schema, verifies every
reviewed schema object, and preserves an outer transaction through nested
savepoints.

### Exact capability policy

Capabilities use the exact `resource:action` form. Wildcards and implied
inheritance are rejected. A role contains explicit allow or deny entries, and the
same role cannot both allow and deny the same capability.

Role bindings are scoped by:

- a specific principal;
- explicit tenants or an explicit all-tenant flag;
- explicit resource type/ID pairs or an explicit all-resource flag; and
- optional expiry and revocation.

Policy evaluation is default-deny. Every active matching deny overrides every
matching allow. Disabled/expired principals, disabled roles, expired/revoked
bindings, missing resources, and resource-less requests against restricted
bindings all fail closed.

A policy query is evaluated in one SQLite snapshot. At most 1,000 matching
bindings may participate; a larger policy returns `policy_too_complex` rather
than truncating evidence into a potentially permissive decision.

`simulate()` returns a structured explanation with matching allow/deny binding
IDs. `authorize()` applies the same policy and raises on denial. The HTTP adapter
must not expose internal denial reasons to untrusted callers.

### Audited impersonation

An impersonation session:

- requires the exact `support:impersonate` capability for the target principal in
  the requested tenant;
- is tenant-bound, reason-required, and limited to one hour;
- permits only the intersection of the actor's and target's current capability;
- rechecks the actor's support permission and both principals on every action;
- allows at most one active session per actor, including under concurrent starts;
- is denied immediately after role, binding, principal, or support-policy change;
- records start, automatic expiry, end, and every enforced allowed/denied action
  in append-only audit events; and
- requires the audit actor ID to equal the authenticated actor principal when a
  session starts or an impersonated action is enforced.

The intersection rule is deliberate: impersonation changes identity/context but
does not let support staff acquire a capability they do not independently hold.

## Security invariants

- No wildcard capability, hidden superuser role, or implicit tenant/resource
  expansion exists in this package.
- Explicit deny always wins over allow within the same request scope.
- Binding expiry cannot exceed principal expiry.
- Role and principal state changes take effect on the next decision; no policy
  cache is trusted.
- Session start takes SQLite's writer lock before policy reads so concurrent
  starts produce one winner and one deterministic conflict.
- Stored audit metadata is canonical JSON and audit rows cannot be updated or
  deleted.
- Principal, role, binding, session, capability, tenant, resource, page, audit,
  and policy-match counts are bounded.
- Policy and administration writes remain part of a caller-owned outer
  transaction and can be rolled back with the related business operation.

## Required signed-application integration

The later application adapter must:

1. map every UI, CLI, and `/api/v1` operation to one reviewed exact capability;
2. derive tenant and resource identifiers from authorized database objects, never
   only from request parameters;
3. keep session, API-token, and impersonation identities explicit in request
   context and audit records;
4. call `authorize_impersonation()`—not the read-only simulator—for every
   impersonated mutation or sensitive read;
5. authorize session termination by the actor, target, or an explicitly capable
   administrator before calling `end_impersonation()`;
6. show a persistent impersonation banner, target identity, tenant, expiry, and
   immediate stop control in every rendered state;
7. require step-up authentication for starting impersonation and destructive
   actions where policy specifies it;
8. translate policy denial to stable generic HTTP 403 responses without leaking
   whether a principal, role, binding, tenant, or resource exists; and
9. add endpoint-by-endpoint object authorization, cross-tenant, concurrent policy
   change, session expiry, reboot, accessibility, and localization tests.

## Not yet delivered

This slice does not complete #143. Remaining work includes:

- integration with the signed application's users, sessions, routes, templates,
  CLI, and existing audit model;
- reviewed built-in role definitions and migration from current role semantics;
- management API/UI/CLI for principals, roles, bindings, simulations, and active
  impersonation sessions;
- HTTP `/api/v1`, OpenAPI, request IDs, rate limits, and stable error envelopes;
- worker runtime and real webhook destination/delivery integration from PR #153;
- step-up authentication, passkeys/SSO policy hooks, and security notifications;
- production VM and external acceptance evidence.

No dependent parity epic may treat this policy package alone as completed RBAC or
support impersonation in the product.
