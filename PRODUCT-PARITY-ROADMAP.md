# HostPanel product parity roadmap

Status: implementation programme
Base reviewed: `main` at `cde5198c8120efe00666e8fa23f01a32e99c7732`

## Goal

Bring HostPanel to practical feature parity with established hosting control panels while preserving its stronger fail-closed installation, signed-update, role-separation, and transactional service-management model.

This programme does not copy another product's interface or implementation. It defines observable outcomes and security boundaries that HostPanel must provide.

## Existing strengths to preserve

The current product already covers the core hosting surface: multi-tenant web hosting, DNS, mail, databases, backups, certificates, firewall policy, monitoring, infrastructure operations, role-based node installation, signed updates, production validation, and configurable web/DNS/database components.

Every parity feature must retain these rules:

- no unpinned root execution;
- explicit tenant and role authorization;
- auditable mutations;
- no secrets in process arguments, logs, URLs, or artifacts;
- preflight before mutation;
- atomic state publication and bounded rollback;
- exact post-change health validation;
- accessible, localized UI states;
- API and UI must call the same domain service layer.

## Delivery rules

1. Do not implement this roadmap in one pull request.
2. Each epic gets its own feature branch, threat model, migration plan, API contract, UI tests, rollback tests, and production acceptance evidence.
3. Foundations are merged before product surfaces that depend on them.
4. A feature is not complete when only the UI exists; CLI/API, authorization, audit, failure recovery, documentation, and tests are required.
5. Existing functionality discovered during implementation is upgraded rather than duplicated.

## Priority 0 — shared platform foundations

### 1. Versioned public API and scoped access tokens

Deliver:

- `/api/v1` JSON API with an OpenAPI document;
- scoped tokens with tenant, resource, action, expiry, last-used time, optional source CIDR, and immediate revocation;
- service-account identities separate from human sessions;
- idempotency keys for mutating operations;
- cursor pagination, stable error envelopes, request IDs, and rate-limit headers;
- API-token UI, CLI, audit events, and emergency global revocation;
- generated read-only API documentation and contract tests.

Security acceptance:

- deny by default;
- token material is shown once and stored only as a verifier;
- no wildcard scope silently expands after an upgrade;
- tenant boundary and object-level authorization tests cover every endpoint;
- destructive operations require explicit destructive scopes and re-authentication policy.

### 2. Audit, events, jobs, and webhooks

Deliver:

- append-only audit event model for actor, tenant, request, target, before/after summary, result, and correlation ID;
- durable background job model with progress, cancellation, retry policy, deduplication, and bounded logs;
- transactional outbox so state changes and emitted events cannot diverge;
- signed webhooks with timestamp, event ID, replay window, rotation, delivery history, and manual retry;
- event filters for sites, DNS, mail, databases, backups, certificates, users, security, deployments, and updates;
- UI and API for job status and audit search/export.

Security acceptance:

- webhook secrets are never retrievable after creation;
- retries are idempotent;
- payloads are tenant-minimized and redact credentials;
- audit retention cannot be shortened by tenant users.

### 3. Capability-based RBAC and feature policy

Deliver:

- explicit admin, operator, reseller, customer, and service-account capabilities;
- role templates plus per-tenant overrides;
- plan/feature policy evaluated by the same authorization layer in UI, CLI, and API;
- impersonation with reason, time limit, visible banner, and complete audit trail;
- resource ownership transfers and delegated administration;
- policy simulator that explains allow/deny decisions.

## Priority 1 — highest customer value

### 4. Git deployment, staging, preview, and rollback

Deliver:

- repository registration using deploy keys or an approved application identity;
- manual and webhook-triggered deployments;
- immutable release directories and atomic current-release switch;
- build command allowlists, environment profiles, secret references, health checks, and deployment logs;
- staging clone, database copy with optional sanitization, search/replace rules, no-index/password protection, preview URL, promote-to-production, and rollback;
- deployment approvals and maintenance windows;
- per-site release history and one-click rollback.

Security acceptance:

- builds run as the site identity with bounded CPU, memory, time, filesystem, and network policy;
- no root hooks from repository content;
- untrusted repository changes cannot modify panel or other tenants;
- database promotion is explicit, backed up, and reversible.

### 5. Migration centre

Deliver:

- import adapters for cPanel, Plesk, DirectAdmin, Hestia/Vesta, generic SSH/rsync, and WordPress-only migration;
- inventory/dry-run report before transfer;
- resumable copy, database consistency method, mail synchronization, DNS planning, certificate handling, quota mapping, and ownership mapping;
- pre-cutover and final delta sync;
- temporary source proxy/live-transfer option where technically safe;
- deterministic verification of files, databases, mailboxes, DNS records, redirects, PHP/runtime versions, and external reachability;
- rollback/cutover checklist and migration evidence export.

Security acceptance:

- source credentials are short-lived and stored in the secret store;
- host keys are pinned;
- imported archives reject links, devices, traversal, ownership abuse, and decompression bombs;
- migration cannot overwrite an existing tenant without an explicit conflict plan.

### 6. Remote backup, restore browser, and disaster recovery

Deliver:

- S3-compatible, Backblaze B2, Wasabi, Google Cloud Storage, Azure Blob, SFTP, and operator-defined rclone remotes;
- client-side encryption, retention, immutability/object-lock awareness, bandwidth windows, and concurrency limits;
- per-tenant and per-site policies;
- restore browser for full tenant, site, database, mailbox, DNS zone, and selected files;
- scheduled restore verification into an isolated target;
- backup health dashboard, stale-backup alerts, and recovery-point/recovery-time reporting;
- cross-node disaster recovery and documented bare-metal recovery.

## Priority 2 — commercial and developer parity

### 7. Reseller, plans, quotas, and white-label operations

Deliver:

- three-level ownership: platform admin → reseller → customer;
- resource packages for sites, domains, aliases, storage, bandwidth, databases, mailboxes, backups, cron jobs, runtime versions, API access, and support level;
- hard/soft limits, grace periods, notifications, suspension states, and optional oversell policy;
- branded hostname, logo, theme, support links, nameservers, email templates, and custom domain;
- reseller backup/restore and user transfer;
- metering/export interface for external billing rather than embedding a payment processor in the privileged core;
- clear separation between entitlement, measured usage, invoice state, and runtime enforcement.

### 8. Application runtimes and process management

Deliver:

- first-class PHP, static, reverse-proxy, Node.js, Python/WSGI/ASGI, and approved container workloads;
- per-site runtime version, start command, health check, environment, secrets, working directory, and resource limits;
- systemd-managed unprivileged processes with restart policy and bounded logs;
- zero-downtime release switch where supported;
- dependency/build cache isolation;
- optional Redis/worker/scheduler processes attached to a site;
- explicit unsupported-runtime and end-of-life warnings.

### 9. WordPress operations centre

Deliver:

- scan/register existing installations;
- install profiles and approved plugin/theme sets;
- SSO, maintenance mode, search indexing, debug controls, cron takeover, cache controls, and security posture checks;
- clone/staging/promote workflows using the shared staging engine;
- plugin, theme, and core update policy with pre-update backup and post-update health checks;
- vulnerability advisory display and bulk actions;
- per-installation file/database backup and restore;
- malware scanning integration with quarantine and evidence, not silent deletion.

## Priority 3 — operations at scale

### 10. Observability and alerting

Deliver:

- server, service, site, database, mail, queue, DNS, certificate, backup, deployment, and job metrics;
- per-site request/error/latency and resource graphs;
- structured log viewer with tenant-safe filtering and download limits;
- alert rules, maintenance windows, deduplication, escalation, and delivery to email, webhook, Slack-compatible endpoints, and PagerDuty-compatible events;
- external uptime checks from independent probes;
- capacity forecasts and noisy-neighbour detection;
- Grafana/Prometheus/OpenTelemetry export without requiring them for core operation.

### 11. Multi-server inventory, DNS cluster, and workload movement

Deliver:

- central node inventory and health;
- role/capability discovery;
- DNS primary/secondary replication with DNSSEC state and failure visibility;
- tenant/site movement between nodes with preflight, delta sync, cutover, and rollback;
- placement constraints and maintenance/drain mode;
- central audit and job visibility without sharing unrestricted root credentials;
- explicit split-brain and network-partition behaviour;
- no claim of high availability until tested with independent failure domains.

### 12. Extension and integration SDK

Deliver:

- signed extension packages and compatibility manifest;
- constrained extension capabilities rather than arbitrary root code;
- navigation, settings, background jobs, events, and API extension points;
- lifecycle hooks with timeouts, resource bounds, and failure isolation;
- extension update/rollback and audit history;
- sample extensions and conformance tests;
- extensions cannot bypass tenant authorization, secret storage, audit, or transactional publication.

## Priority 4 — UX parity and operator efficiency

### 13. Self-service tools

Deliver or verify and strengthen:

- file manager with safe upload, archive extraction, editor, ownership preservation, and trash/recovery;
- restricted web terminal tied to site identity and plan permission;
- SSH key and SFTP account management;
- cron/scheduled-task UI with validation and execution history;
- database administration SSO;
- mail queue, logs, autoresponders, filters, aliases, catch-all policy, and delivery diagnostics;
- DNS templates, DNSSEC workflow, bulk import/export, and propagation checks;
- certificate inventory, renewal history, chain diagnostics, and expiry alerts.

### 14. Security and identity upgrades

Deliver:

- WebAuthn/passkeys and recovery codes;
- TOTP 2FA and step-up authentication for destructive actions;
- OIDC/SAML SSO for administrator organisations;
- session/device inventory and global revocation;
- login risk controls, brute-force visibility, and administrative IP policy;
- security headers, CSP reporting, WAF integration, vulnerability posture, and actionable remediation;
- downloadable tenant security report with no secrets.

## Cross-cutting acceptance matrix

Every epic must include:

- schema migration and downgrade/rollback decision;
- API, CLI, and UI authorization parity;
- audit events and job progress;
- localization keys and accessibility coverage;
- tenant-isolation tests;
- symlink, hardlink, pathname-swap, ownership, permission, size-limit, timeout, partial-write, and concurrency tests where files/processes are involved;
- restart/reboot persistence tests;
- backup and restore behaviour;
- documentation and operator recovery procedure;
- production VM acceptance on the exact reviewed commit.

## Recommended merge sequence

1. API/auth foundation.
2. Audit/events/jobs/webhooks.
3. Capability policy and reseller ownership model.
4. Remote backup foundation.
5. Git deployment and staging.
6. Migration centre.
7. Observability and alerting.
8. Reseller plans and white-label UX.
9. Application runtimes.
10. WordPress operations centre.
11. Multi-server and DNS cluster.
12. Extension SDK.
13. Self-service and identity enhancements.

## Definition of product parity

HostPanel reaches the target when an administrator, reseller, customer, and automation client can complete the common hosting lifecycle—provision, deploy, secure, observe, back up, migrate, recover, scale, and decommission—without SSH-only hidden steps, privilege ambiguity, or unaudited mutations, and when the same workflows pass tenant-isolation and recovery testing on supported operating systems.
