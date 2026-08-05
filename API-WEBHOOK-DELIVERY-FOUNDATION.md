# HostPanel webhook destination and delivery foundation

This document defines the sixth implementation slice of product-parity epic #143.
It is stacked on durable worker PR #156 and consumes the transactional outbox and
HMAC signing contract introduced in PR #153.

The slice provides tenant-bound webhook destination persistence and a reviewed
delivery coordinator. It does not include a production network client, DNS
resolver, daemon installation, signed-runtime mounting, or management UI/CLI.

## Delivered contract

The `hostpanel_api_webhooks` package provides:

- tenant-bound destination creation, optimistic update/secret-reference rotation,
  disablement, and exact event subscriptions;
- storage of secret references only—never webhook signing secret bytes;
- exact SQLite table, index, foreign-key, and pragma validation;
- caller-owned transaction preservation through nested savepoints;
- canonical public HTTPS URL validation with default port only;
- rejection of credentials, query secrets, fragments, IP literals, reserved
  hostnames, traversal aliases, malformed/noncanonical paths, and private peers;
- validation that every resolved address is globally routable and the connected
  peer belongs to the reported DNS result;
- deterministic canonical event envelopes and injected PR #153-compatible HMAC
  signing;
- fixed delivery headers, bounded payloads/timeouts, no redirects, and verified
  TLS requirements;
- exact status classification: 2xx delivered; 408/425/429/5xx retryable; other
  responses terminal;
- bounded integer `Retry-After` support and deterministic bounded exponential
  retry fallback;
- generic persisted transport errors without secret or response-body storage; and
- lease-loss behavior that performs no stale second transition.

## Transport contract

The injected production transport must:

1. resolve the canonical destination hostname immediately before connecting;
2. return every address considered in `resolved_ips`;
3. connect only to one of those exact addresses and report it as `peer_ip`;
4. verify the certificate and SNI hostname against the canonical hostname;
5. reject redirects rather than following them;
6. enforce the supplied connect/read timeouts and the 64 KiB request-body bound;
7. read at most 4 KiB of response body for protocol draining and never expose it
   to durable error state; and
8. avoid proxy/environment behavior unless a later reviewed transport policy
   explicitly validates the proxy boundary.

The coordinator revalidates that every resolved address and the connected peer
are public. A transport reporting a private address, mixed public/private answer,
unverified TLS, redirect, or mismatched peer fails terminally.

## Destination and secret lifecycle

- Destinations are scoped to one tenant and use optimistic `version` checks.
- Event filters contain exact event types; no wildcard subscriptions exist.
- Disablement is irreversible in this slice and immediately blocks new delivery.
- Rotation replaces only the secret reference. Secret bytes are resolved at each
  delivery attempt through an injected provider and are never persisted.
- Enqueueing and destination audit integration remain the responsibility of the
  later signed API adapter; the delivery coordinator fails closed if an outbox
  event references a missing, disabled, or unsubscribed destination.

## Required production integration

A later operations/integration slice must:

- implement the reviewed DNS-pinning HTTPS transport contract;
- resolve secret references from a protected secret manager with audit and
  rotation procedures;
- expose destination CRUD/rotation/disable APIs through signed `/api/v1` routes
  with PR #152 token and PR #154 RBAC enforcement;
- enqueue outbox events only after trusted tenant/destination validation and in
  the same transaction as the originating state change;
- add append-only audit events for destination lifecycle and delivery policy
  changes;
- register the delivery coordinator as a fixed PR #156 worker handler or a
  separately supervised reviewed process;
- provide bounded observability without payload, URL-token, secret, or response
  body leakage;
- test resolver rebinding, IPv4/IPv6 mixed answers, certificate/SNI failure,
  timeout, retry, rotation, disablement, crash recovery, reboot, and production
  VM behavior; and
- document destination ownership migration and rollback.

## Not yet delivered

This slice does not complete #143. It includes no concrete HTTPS implementation,
production resolver, secret manager, daemon/systemd unit, signed HTTP endpoints,
UI/CLI, delivery dashboard, audit export, domain endpoints, or production VM
validation. It must remain stacked and draft until #152–#156 are integrated and
hosted exact-head workflows run successfully after billing blocker #13 is
resolved.
