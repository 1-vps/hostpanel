# HostPanel append-only audit foundation

This document defines the seventh implementation slice of product-parity epic #143.
It is stacked on webhook delivery PR #157 and provides a central audit contract for
later token, HTTP, RBAC, impersonation, job, and webhook adapters.

The slice is a framework-independent SQLite library. It does not mount routes,
render an audit UI, install a daemon, delete retained events, or modify the signed
HostPanel runtime.

## Delivered contract

The `hostpanel_api_audit` package provides:

- tenant-bound immutable events with actor kind/ID, optional effective principal,
  action, outcome, optional target, request ID, canonical source IP, reason code,
  redacted metadata, and retention class;
- one globally ordered append stream with monotonic timestamps;
- a SHA-256 hash chain rooted at a domain-separated genesis hash;
- durable event count, last sequence, and chain head so tail deletion and stale
  head state are detectable;
- exact SQLite table, index, trigger, pragma, and schema-shape validation;
- writer-lock serialization and caller-owned transaction preservation through
  nested savepoints;
- recursive metadata redaction before persistence, hashing, search, and export;
- tenant-required exact filters and stable `(occurred_at, sequence)` cursors;
- deterministic canonical JSONL and CSV export with spreadsheet-formula hardening;
- deterministic export SHA-256 digests;
- policy-derived expiry timestamps for standard, security, compliance, and
  permanent retention classes; and
- ordered retention candidates and deterministic archive manifests without any
  deletion API.

## Redaction boundary

Metadata is bounded to 32 KiB, sixteen levels, and 2,000 values. Floating-point
values, NUL text, invalid object keys, unsupported objects, excessive depth, and
excessive size fail closed. Keys resembling credentials, authorization, cookies,
passwords, secrets, tokens, API keys, private keys, signatures, or sessions are
replaced with `[REDACTED]`. Bearer values and PEM private-key values are also
redacted even when their key name is not sensitive.

Callers remain responsible for providing metadata allowlists. Automatic redaction
is a defense-in-depth boundary, not permission to send arbitrary request bodies,
headers, command output, configuration files, or third-party responses to audit.

## Integrity boundary

`verify_chain()` validates:

1. exact reviewed schema objects are still present;
2. sequences are contiguous and timestamps never move backwards;
3. every `previous_hash` equals the preceding event hash;
4. every event hash matches the canonical redacted event document; and
5. stored event count, last sequence, and head hash match the complete stream.

This makes accidental corruption, ordinary SQL update/delete attempts, stale
head writes, row modification, and tail deletion detectable. A later production
operations slice must externally anchor signed chain heads or export manifests;
a database administrator who can rewrite the entire database and recompute every
hash is outside this local hash-chain threat boundary.

## Retention boundary

Retention in this slice only identifies ordered archive candidates and generates
a deterministic manifest containing sequence, event ID, tenant, timestamps, and
event hash. It never deletes or mutates audit events. A later reviewed archive
workflow must atomically export, externally anchor/sign, verify durable object
storage, record an audit archive checkpoint, and only then introduce any
retention deletion mechanism with legal-hold and rollback controls.

## Required production integration

Later signed adapters must:

- emit central events for token creation/revocation/global revoke, authorization
  decisions, impersonation lifecycle and actions, idempotency conflicts, job
  lifecycle, webhook destination lifecycle/delivery policy, and privileged
  configuration changes;
- derive tenant, actor, effective principal, target ownership, and source address
  from trusted authenticated context rather than request parameters;
- keep audit append in the same transaction as the state change where practical,
  or use the existing transactional outbox for cross-database boundaries;
- expose search/export through reviewed `/api/v1` routes with PR #152 token and
  PR #154 capability enforcement;
- provide accessible/localized UI and CLI parity without widening filters or
  tenant scope;
- enforce export authorization, bounded pages, retention/legal-hold policy, and
  download expiry; and
- externally anchor chain heads and validate migration, rollback, reboot, backup,
  restore, and production VM behavior.

## Not yet delivered

This slice does not complete #143. It contains no audit HTTP endpoints, UI/CLI,
external chain anchor, signed archive, object-storage integration, legal holds,
retention deletion, cross-database atomic adapter, production service, migration
from legacy logs, or production VM validation. Keep it stacked and draft until
PRs #152–#157 are integrated and hosted exact-head workflows execute after
billing blocker #13 is resolved.
