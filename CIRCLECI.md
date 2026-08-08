# HostPanel CircleCI exact-head gate

This is the replacement control-plane contract for Buildkite. It is deliberately
inactive until an administrator completes the external setup and a real exact-head
run passes. Do not use CircleCI as a required status or retire Buildkite based on
static inspection.

## Two-stage bootstrap

The trusted config files must exist on protected `main` before CircleCI can load
them. Bootstrap therefore happens in two separate changes:

1. Merge the reviewed bootstrap change with the repository's existing required
   checks and review policy while CircleCI pipelines and triggers remain disabled.
2. From the resulting `main`, open a separate activation PR. Configure CircleCI
   from protected `main`, emit a fresh supported PR event, and require the first
   exact-head run to pass before enabling a required CircleCI status or retiring
   Buildkite.

Never point the config source at an activation PR branch to work around bootstrap.

## Required CircleCI project shape

Use the CircleCI **GitHub App** integration for `1-vps/hostpanel`. GitHub OAuth is
not accepted. Create two pipeline definitions:

1. `hostpanel-pr`: config source `1-vps/hostpanel`, fixed branch `main`, path
   `.circleci/pr.yml`; checkout source `1-vps/hostpanel`; trigger only non-fork
   pull-request events `opened`, `synchronize`, `reopened`, and `ready_for_review`.
2. `hostpanel-main`: config source `1-vps/hostpanel`, fixed branch `main`, path
   `.circleci/main.yml`; checkout source `1-vps/hostpanel`; trigger only pushes to
   exact branch `main`.

The config source must never follow the PR branch. Runtime checks require the PR
config SHA to equal the GitHub PR base SHA and the checkout revision to equal the
PR head SHA. Main builds require config, checkout, and push SHA equality.

Create exactly two self-hosted resource classes:

- `1-vps/hostpanel-ci`
- `1-vps/hostpanel-qemu`

Machine Runner 3 must use `runner.mode: single-task`, run tasks as an unprivileged
`circleci` user, disable task-agent caching, stop rather than restart after the
task, and shut down the disposable VM in `ExecStopPost`. Destroy the VM and disk
after every job. Forward runner logs to independent storage before destruction.

Do not configure CircleCI contexts, project environment variables, caches,
workspaces, test-result uploads, SSH reruns, or fork PR builds. Artifacts are
forbidden except for the single sanitized and sealed QEMU evidence archive produced
by the trusted `main` job. The only CircleCI-managed credential is the read-only
HTTPS checkout credential installed by the GitHub App integration. The trusted
config removes local Git credential helpers and HTTP authorization headers, removes
credential files, disables system/global Git configuration and interactive
credential prompts, and removes any SSH fallback before repository commands run.

## Worker images

CI workers require Python 3, PyYAML, Bash, Git, OpenSSL, ShellCheck, Docker, GNU
coreutils `timeout`, the pinned CircleCI CLI used to compile both configs, and the
packages required by `test-matrix.sh`. QEMU workers also require KVM, QEMU,
`setfacl`, and the production-validator dependencies.

The runner registration token belongs only in the root-owned runner service
configuration and must not be readable by the task user. QEMU main workers receive
one short-lived GitHub Contents:Read token at
`/run/hostpanel-circleci/qemu-repo-token`, single-linked and mode `0400` or `0600`.
It is consumed and unlinked before QEMU executes. Never put it in a CircleCI
context, project variable, cache, workspace, artifact, or command argument. Only
the sanitized, sealed QEMU evidence archive may be uploaded as a CircleCI artifact;
the raw evidence directory and credentials must never be uploaded.

## First trusted run after bootstrap

1. Prove both pipeline definitions use the exact config source, path, checkout
   source, and trigger filters above.
2. Prove no unexpected project variables, contexts, SSH keys, schedules, API
   triggers, or resource classes exist.
3. Provision disposable `hostpanel-ci` capacity in single-task mode.
4. Emit one fresh `ready_for_review` event for the unchanged target PR head.
5. Require the CircleCI pipeline revision and GitHub PR head SHA to match exactly.
6. Require all fifteen PR jobs to pass with no retries, approvals, skips, caches,
   workspaces, artifacts, or parallel/matrix substitutions.
7. Retain the sanitized, sealed QEMU archive for `main`, destroy every worker, and
   retain the external runner logs plus CircleCI pipeline and job identifiers.

After merge, repeat on the new exact `main` SHA with the sixteen-job main graph,
including QEMU on the dedicated resource class. Only then make the stable CircleCI
status check required in GitHub branch protection.
