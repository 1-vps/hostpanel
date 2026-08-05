# HostPanel durable API worker foundation

This document defines the fifth implementation slice of product-parity epic #143.
It is stacked on the API transport PR #155 and consumes the durable job contract
introduced in PR #153.

The slice provides a fixed-registry, framework-independent worker runtime. It
does not install or start a system service, register production handlers, mount
HTTP routes, or execute shell commands.

## Delivered contract

The `hostpanel_api_worker` package provides:

- an immutable exact job-type to callable registry;
- structural compatibility checks for the reviewed `ApiControlStore` job API;
- one-job claim/execute/transition semantics;
- cooperative lease heartbeat, monotonic progress, cancellation checkpoints, and
  bounded durable logging through `JobContext`;
- recursively read-only handler payloads;
- mapping-or-null result enforcement;
- deterministic bounded exponential retry and bounded explicit retry delays;
- terminal fail-closed handling for unknown job types and invalid retry requests;
- redacted durable errors with detailed exceptions available only through an
  injected out-of-band reporter;
- lease-loss handling that performs no second completion/failure transition;
- bounded run cycles and an injected idle sleep for supervised loops; and
- no dynamic imports, subprocesses, shell execution, listener startup, or
  service-manager integration.

## Handler contract

A reviewed handler has the shape:

```python
def handler(context: JobContext, payload: Mapping[str, object]) -> Mapping[str, object] | None:
    context.checkpoint(10)
    context.log("info", "reviewed milestone reached")
    # perform one reviewed, idempotent operation
    context.checkpoint(90)
    return {"resource_id": "reviewed-result"}
```

Handlers must:

1. be registered directly in the immutable `HandlerRegistry` at process startup;
2. never be selected from a module path, payload value, database string, or user
   input;
3. treat payloads as read-only and validate their domain schema before side
   effects;
4. call `checkpoint()` at reviewed interruption boundaries so cancellation and
   lease ownership are re-evaluated;
5. make external side effects idempotent or reconcile them before retry;
6. emit only non-secret bounded log messages; and
7. return a JSON-compatible mapping or `None`.

## Failure semantics

- An unregistered job type is terminally failed as `unregistered job type`.
- `RetryableJobError` schedules a future retry only within configured bounds and
  only while the durable store reports remaining attempts.
- An invalid retry request is terminally failed as `invalid retry request`.
- An unexpected handler exception is durably recorded only as
  `job handler failed`; the full exception is sent to the injected reporter.
- A cancellation observed before or during a checkpoint is passed to
  `ApiControlStore.fail_job()`, whose transactional state machine resolves it to
  `cancelled`.
- Any lease loss during heartbeat, logging, completion, or failure returns a
  `lease_lost` outcome and performs no compensating transition with a stale
  lease.

## Required production integration

A later operations/integration slice must:

- construct `ApiControlStore` on a reviewed SQLite connection and migrate it
  before worker startup;
- create a fixed registry containing only signed, reviewed production handlers;
- give each process its own database connection and stable worker ID;
- supervise the loop with systemd or another reviewed service manager;
- provide graceful stop signalling and deployment drain behavior;
- define per-handler payload/result schemas, idempotency/reconciliation rules,
  progress milestones, retry policy, timeout budget, and secret-redaction tests;
- ensure external calls have bounded connect/read timeouts and cancellation-safe
  boundaries;
- add reboot, crash recovery, lease expiry, duplicate delivery, and production VM
  acceptance tests; and
- expose job status/cancellation/log APIs through the signed `/api/v1` adapter.

## Not yet delivered

This slice does not complete #143. It includes no production handler registry,
systemd unit, CLI, signed-runtime integration, domain endpoint, webhook delivery,
audit export, UI, or production VM validation. It must remain stacked and draft
until #152, #153, #154, and #155 are integrated and hosted exact-head workflows
run successfully after billing blocker #13 is resolved.
