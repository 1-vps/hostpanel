# HostPanel Buildkite bootstrap

This repository contains the reviewed repository-side control plane for a hardened Buildkite bootstrap. The Buildkite organization, cluster, queues, agents, credentials, network controls, and branch-protection status are external infrastructure and must be created only after this bootstrap configuration is merged.

## Trust model

Use three queues:

- `hostpanel-upload`: trusted no-checkout signer/uploader. It never checks out or executes repository code.
- `hostpanel-ci`: disposable CI worker for repository checks and Docker-backed installer matrix tests.
- `hostpanel-qemu`: disposable KVM worker for privileged post-merge QEMU acceptance.

The upload queue alone receives the private signing JWKS among Buildkite agents. CI/QEMU never receive the GitHub checkout key as a Buildkite Secret or artifact. `HP_QEMU_REPO_TOKEN` is the only Buildkite secret intentionally exposed to repository code and is restricted to webhook-triggered, non-PR `main` jobs on the QEMU queue.

PR jobs are accepted only for a same-repository PR. Fork PRs, non-webhook sources, and contradictory PR repository metadata fail closed before checkout or repository command execution.

## Static bootstrap

Configure Buildkite Pipeline Settings with a statically signed bootstrap that does not check out repository code:

```yaml
steps:
  - label: ":pipeline: Upload reviewed pipeline"
    key: "upload-reviewed-pipeline"
    command: "/usr/local/libexec/hostpanel-upload-pipeline"
    checkout:
      skip: true
    agents:
      queue: "hostpanel-upload"
```

All agents keep `verification-failure-behavior="block"`. Therefore the Pipeline Settings YAML above must be statically signed before any `hostpanel-upload` worker is started; otherwise the first upload job is unsigned and is correctly blocked before it can sign the dynamic pipeline.

After the Buildkite pipeline exists and the signing/verification JWKS pair has been generated on a trusted operator host, sign the Pipeline Settings steps with Buildkite's static signing flow:

```bash
buildkite-agent tool sign \
  --graphql-token '<buildkite-write-pipelines-token>' \
  --jwks-file /root/signing-private.jwks \
  --jwks-key-id hostpanel-2026-08 \
  --organization-slug '<organization-slug>' \
  --pipeline-slug '<pipeline-slug>' \
  --update
```

The GraphQL token is an operator credential used only for this Pipeline Settings update and must never be installed on any worker. The same public verification key is then provisioned to all worker types, while among Buildkite agents the private signing JWKS is provisioned only to `hostpanel-upload`. Re-run the static signing command whenever the Pipeline Settings bootstrap step changes or signing keys rotate, before exposing a blocking-verification upload worker to that step.

The upload worker also has agent-level `skip-checkout=true`. Its root-installed uploader reads only `/etc/buildkite-agent/hostpanel-pipeline.yml`, validates its root-owned SHA-256 policy, and uploads with `--no-interpolation`, `--reject-secrets`, and `--reject-parse-warnings`.

## Private checkout credential

The reusable GitHub deploy private key must never be stored in Buildkite Secrets. Buildkite secret authorization applies to the job context, so repository-controlled code in an authorized job can request secrets available to that context. The checkout key therefore remains local provisioning material only.

Generate a read-only deploy key with:

```bash
.buildkite/operator/generate-checkout-key.sh /root/hostpanel-checkout-key
```

Add only the `.pub` half to GitHub with Contents: Read-only access. For each fresh CI/QEMU VM, place a root-owned mode `0600` copy of the private key on root-controlled tmpfs before provisioning. `/run` must be `tmpfs`, and runner workers must have no active swap.

The provisioner consumes that input, stages `/run/hostpanel-buildkite/checkout-deploy-key` as `buildkite-agent:buildkite-agent` mode `0400`, and deletes the operator input. The bound wrapper later transfers the dedicated tmpfs directory to `buildkite-agent:buildkite-agent` mode `0700` immediately before agent startup so the trusted checkout hook can unlink its key.

The root-installed checkout hook:

- accepts only `git@github.com:1-vps/hostpanel.git`;
- requires a full lowercase `BUILDKITE_COMMIT` SHA;
- strips Git/SSH/proxy injection variables;
- disables global/system Git config and repository hooks;
- pins the reviewed GitHub `known_hosts` keys;
- performs at most three exact-SHA fetch attempts, each bounded to 600 seconds;
- verifies exact origin, detached HEAD, and a clean worktree;
- validates the OpenSSH key header using the same `O_NOFOLLOW`-opened descriptor bytes used for metadata checks;
- destroys the tmpfs checkout key before repository command execution.

The pipeline contains no `HP_GIT_SSH_KEY`, no `checkout.ssh_secret`, and no source-artifact checkout relay.

## Provisioning provenance

Record the exact reviewed merged `main` commit outside the worker checkout. Do not derive this trust anchor from the worker's local Git refs. Pass it explicitly as:

```text
--source-commit <exact-reviewed-main-sha>
```

The operator-invoked `configure-bound-agent.sh` is the bootstrap trust anchor. Before it executes or installs any other repository file as root, it uses only system Git plus the externally recorded commit SHA to create a root-only snapshot under `/run`. Git replacement objects, hooks, fsmonitor, global/system configuration, and inherited repository-selection variables are disabled during this bootstrap. The verifier, base provisioner, policy hooks, uploader, systemd drop-in, and pipeline file are materialized byte-for-byte from Git blobs belonging to that exact commit and re-hashed before use.

After the snapshot exists, no repository-worktree file is executed or installed as root. `configure-agent.sh` runs from the root-only snapshot and therefore resolves every hook and pipeline source from the same snapshot. The pre-command hook and ephemeral systemd policy are also installed from that trusted copy. The worktree remains only a verification target and Git object source.

`verify-provisioning-source.py` requires:

- exact origin `git@github.com:1-vps/hostpanel.git`;
- `HEAD` equals the externally supplied SHA;
- local `refs/remotes/origin/main` equals that same SHA;
- clean worktree;
- every provisioning source file matches the reviewed commit blob;
- an isolated Git environment with inert `HOME`, no global/system Git config, no inherited repository-selection/config-injection variables, `core.hooksPath=/dev/null`, `core.fsmonitor=false`, and `GIT_NO_REPLACE_OBJECTS=1`.

The trusted snapshot verifier checks the source before installation and again immediately before agent start.

## Agent requirements

Use Ubuntu 24.04 disposable workers and Buildkite agent 3.134.0 or newer. The agent configuration enforces strict checkout authority, no command evaluation, no plugins, no local repository hooks, no submodules, no SSH keyscan, the exact allowed repository, strict single hooks, verification JWKS blocking, one-job disconnect, and no feature reporting.

CI workers are Docker/root-equivalent and therefore must be single-job disposable VMs with no inbound SSH, restricted egress, and no production network route. QEMU workers expose KVM but not Docker. Upload workers expose neither Docker nor KVM.

All workers use systemd `Restart=no` and `LimitCORE=0`.

## One-job worker capacity

`disconnect-after-job=true` and `Restart=no` are security boundaries, not a static-pool deployment model. Buildkite schedules jobs but does not recreate these VMs. The external worker lifecycle controller must therefore supply a fresh worker instance for every scheduled job. Operators must not enable the pipeline with one static worker per queue.

The current dynamic pipeline consumes exactly:

- 15 fresh `hostpanel-ci` one-job worker instances per build: one pipeline-contract job, five core-validation jobs, and nine supported-OS jobs;
- one fresh `hostpanel-qemu` worker for each eligible `main` build;
- the static no-checkout bootstrap additionally consumes one fresh `hostpanel-upload` worker per build.

The 15 CI instances do not all have to be prestarted when a tested external autoscaler deterministically launches a fresh VM for every queued CI job. Without such an autoscaler, pre-provision all 15 CI workers for each build. For the first full post-merge `main` activation run without autoscaling, provision 17 fresh worker instances in total: 1 upload + 15 CI + 1 QEMU. Concurrent builds multiply these requirements; never let two jobs share or reuse a worker.

Every generated worker gets its own registration token. Every CI/QEMU worker gets its own tmpfs provisioning copy of the read-only deploy key, consumed before repository code runs. A fleet controller must create workers from the exact externally recorded merged commit, apply the queue-specific network restrictions, run the worker smoke test, and destroy the VM/disk after its single job.

## One-time registration token

Create a separate short-lived, allowed-IP-restricted Buildkite registration token for each worker. Provide it to the bound provisioner as a root-owned mode `0600` input file.

The base provisioner consumes the input and stages `/etc/buildkite-agent/agent-token` as root-only mode `0600`. Agent configuration uses:

```text
token="fd://3"
```

systemd opens the file as inherited FD 3 before the agent process drops privileges. The final ephemeral drop-in clears the service-side `ExecStartPost` token deletion because `ProtectSystem=full` makes `/etc` read-only inside the service namespace. After `systemctl restart`, `configure-bound-agent.sh` first confirms the agent is active, then deletes `/etc/buildkite-agent/agent-token` itself as root outside the service namespace and verifies the path is gone before releasing its fail-closed cleanup trap. No `ReadWritePaths=/etc` exception is used.

The service uses `Restart=no`. After the worker connects and passes its smoke test, revoke that registration token in Buildkite before enabling the pipeline. Revoking the registration token prevents new registrations without interrupting the already-connected agent session. A consumed one-job worker must not be restarted or reconnected; replace it with a freshly provisioned VM.

## Agent provisioning

Install Buildkite agent 3.134.0 or newer from Buildkite's signed stable Ubuntu repository. Provision from the exact reviewed merged `main` commit. Record that SHA outside the worker checkout and pass it explicitly as `--source-commit`; the verifier rejects the worker even when local `HEAD` and `refs/remotes/origin/main` agree if they do not equal that externally reviewed SHA.

All operator-supplied trust and credential files are read with `O_NOFOLLOW` and descriptor metadata checks before installation. The registration token, public verification JWKS, upload signing JWKS, and runner checkout key are materialized from those validated descriptor bytes into a root-owned mode `0700` staging directory under `/run`; installation uses only the staged mode `0600` copies, never the original paths. The staging directory is removed on exit. Private token/signing/checkout inputs must be root-owned mode `0600`; the verification JWKS must be a root-owned regular file that is not group- or world-writable. This prevents a writable parent directory from swapping a new trust root or credential into the path after validation.

CI example:

```bash
sudo .buildkite/agent/configure-bound-agent.sh \
  --pipeline-id '<pipeline-uuid>' \
  --source-commit '<exact-reviewed-main-sha>' \
  --queue hostpanel-ci \
  --pipeline-slug '<pipeline-slug>' \
  --token-file /root/buildkite-agent-token \
  --verification-jwks /root/verification-public.jwks \
  --checkout-key /run/hostpanel-provisioning/checkout-deploy-key \
  --start
```

QEMU uses the same command with `--queue hostpanel-qemu`.

Upload example:

```bash
sudo .buildkite/agent/configure-bound-agent.sh \
  --pipeline-id '<pipeline-uuid>' \
  --source-commit '<exact-reviewed-main-sha>' \
  --queue hostpanel-upload \
  --pipeline-slug '<pipeline-slug>' \
  --token-file /root/buildkite-agent-token \
  --verification-jwks /root/verification-public.jwks \
  --signing-jwks /root/signing-private.jwks \
  --signing-key-id hostpanel-2026-08 \
  --start
```

The base `configure-agent.sh` never starts an agent by itself. Only `configure-bound-agent.sh` may start the service after immutable pipeline UUID and externally reviewed source-commit policies are installed and reverified. For CI/QEMU it also transfers the dedicated `/run/hostpanel-buildkite` tmpfs credential directory to `buildkite-agent:buildkite-agent` mode `0700` only after root provisioning is complete, so the trusted checkout hook can unlink its mode `0400` key before repository commands run. The upload worker keeps that directory root-controlled.

Before exposing a worker to jobs, run:

```bash
sudo .buildkite/operator/smoke-test-agent.sh \
  '<queue>' \
  '<pipeline-slug>' \
  '<pipeline-uuid>'
```

For CI/QEMU this smoke test runs before the first checkout, so it verifies that the one-time checkout key is present with exact ownership/mode and is a valid unencrypted OpenSSH key. The first checkout must then destroy it before command execution. The upload queue is verified to contain no GitHub checkout key.

## QEMU repository token

`HP_QEMU_REPO_TOKEN` is the only Buildkite secret intentionally exposed to repository code. It is a short-lived GitHub credential with Contents: Read-only access to this repository and is usable only for the trusted post-merge QEMU workflow.

Render its secret policy:

```bash
.buildkite/operator/render-secret-policy.py qemu \
  --pipeline-id '<pipeline-uuid>' \
  --qemu-queue-id '<qemu-queue-uuid>'
```

The policy binds access to pipeline UUID, webhook source, `main`, and the QEMU queue UUID. `run-qemu.sh` retrieves it only after non-secret QEMU contracts pass, passes it only to the VM-acceptance child process, and unsets it before evidence preparation, sanitization, sealing, and artifact upload.

## Network and lifecycle requirements

Every worker VM must have:

- no inbound SSH or other public management listener;
- restricted egress appropriate to its queue;
- no route to production hosts or management networks;
- a fresh disk/image for one job only;
- no active swap on CI/QEMU workers before a checkout key is supplied;
- destruction of the VM and disk after its one Buildkite job.

Do not reuse a worker whose agent disconnected, failed, or consumed its one-time credentials.

## Bootstrap merge rule

This repository configuration is inactive until the external Buildkite organization, cluster, queues, credentials, agents, GitHub App connection, and pipeline are created. The first bootstrap PR can therefore merge without a Buildkite status as long as it remains inactive and no Buildkite resources are provisioned from the PR branch.

After the bootstrap is merged:

1. Record the exact merged `main` commit outside every worker checkout.
2. Connect the Buildkite GitHub App; create the cluster, three queues, and pipeline, configure exact repository `git@github.com:1-vps/hostpanel.git`, and place the reviewed static no-checkout YAML in Pipeline Settings.
3. Generate the signing/verification JWKS pair on a trusted operator host. Before starting any upload worker, statically sign the Pipeline Settings YAML with `buildkite-agent tool sign ... --update` using a temporary operator GraphQL token with `write_pipelines`; do not put that token on an agent.
4. Add the read-only GitHub deploy public key; do not create a Buildkite checkout secret.
5. Configure an external lifecycle controller that supplies one fresh upload worker per build, 15 fresh CI workers per build, and one fresh QEMU worker for eligible `main` builds. If no autoscaler is available for the bootstrap run, pre-provision all 17 workers for the first full `main` build.
6. Create a separate short-lived registration token for every worker instance the lifecycle controller will launch.
7. Provision fresh workers from the exact merged commit and pass that same externally recorded SHA as `--source-commit`. Use the same public verification JWKS everywhere; among agents provision the private signing JWKS only to upload workers.
8. Run `smoke-test-agent.sh` on each worker type and prove the lifecycle controller can replace a consumed CI worker before the pipeline is enabled.
9. Revoke registration tokens after their corresponding one-job agents are connected and smoke-tested.
10. Enable the pipeline and run the exact merged `main` commit, including all 15 CI jobs and QEMU acceptance.
11. Confirm checkout credentials are destroyed before repository code runs and every worker VM/disk is destroyed after its job.
12. Only after a successful exact-head Buildkite run should the Buildkite status become a required branch-protection check.

Every later Buildkite control-plane change requires an exact-head Buildkite PR run before merge and reprovisioning of affected disposable workers from the reviewed commit.
