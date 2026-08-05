# HostPanel production readiness

<!-- {{HOSTPANEL_RELEASE_VERSION}}=3.4.1 -->
<!-- {{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5 -->
<!-- {{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable -->
<!-- {{HOSTPANEL_PUBLICATION_ALLOWED}}=false -->

This checklist applies to deployable HostPanel release **3.4.1**, derived from
signed base **3.4.0-hardened-r5**. It does not itself authorize production
publication. The authoritative state and current blockers are defined in
[`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json).

A release is not production-ready until every selected role and recovery path
passes on the exact reviewed commit, operating system, and infrastructure intended
for deployment, and all legal terms are approved.

## Resolve the exact reviewed revision

Do not hard-code an old installer overlay from this document. Before each
acceptance run, record the full 40-character commit SHA approved for that run and
verify that [`SETUP.md`](SETUP.md) binds every root-executed file to reviewed Git
objects.

Record:

- exact reviewed commit SHA and required Git blob IDs;
- signed source archive checksum and signature verification result;
- target operating-system image and kernel;
- node roles and enabled services;
- public/private addresses, panel hostname, SSH port, and administrative CIDR;
- provider snapshot identifier;
- customer-data filesystem and quota mechanism.

## Prepare a disposable VM

Use a fresh systemd VM of the exact target operating system. Keep provider-console
access available and create a provider-level snapshot before installation. Do not
start with a production server or a host containing customer data.

Supported systems and resource minimums come from `RELEASE-MANIFEST.json` and are
validated by `tools/validate_release_manifest.py`.

## Install from the pinned commit

Follow [`SETUP.md`](SETUP.md) to perform authenticated, blob-verified downloads.
Run the non-mutating preflight first, then the mutating installation using the
same full commit SHA, roles, hostname, and network policy.

Preserve `/var/log/hostpanel-install.log` and the root-owned installer snapshot.
Confirm:

```bash
test "$(tr -d '[:space:]' < /opt/hostpanel/VERSION)" = 3.4.1
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor --quiet
```

## Initial validation

Run the blob-verified production validator:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

Resolve every failure before continuing. Warnings require explicit review even
when they do not automatically fail validation.

## Verify reboot persistence

Record the current boot ID only after initial checks pass:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --prepare-reboot

sudo reboot
```

Reconnect over the configured SSH port and run:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --post-reboot
```

The post-reboot mode must verify a changed kernel boot ID and persistent service,
listener, firewall, certificate, and storage state.

## End-to-end role tests

The local validator is necessary but insufficient. From a separate network, test:

- panel login, session policy, and step-up behavior over trusted TLS;
- customer site creation, PHP execution, static files, redirects, logs, and TLS;
- database creation, least-privilege authentication, backup, and restore;
- authoritative DNS delegation, DNSSEC where enabled, and external resolution;
- inbound/outbound mail, submission, IMAP, SPF, DKIM, DMARC, reverse DNS, and queue diagnostics;
- scheduled backup completion and restoration into a disposable account;
- quota enforcement on the actual customer-data filesystem;
- SSH reconnection after firewall reload and reboot;
- public IPv4 and IPv6 behavior where applicable;
- tenant isolation for every enabled UI, CLI, and API workflow.

Do not treat local port checks as proof of external DNS delegation, trusted TLS,
mail deliverability, or tenant isolation.

## Backup and destructive recovery

On a disposable VM with a confirmed provider snapshot:

1. create application, database, mailbox, DNS, and certificate state;
2. create and verify a backup;
3. delete or corrupt the test state through reviewed failure-injection tooling;
4. restore it and verify external behavior, ownership, permissions, and quotas;
5. exercise installer rollback and provider-snapshot recovery;
6. verify bounded, redacted evidence and absence of secret material.

The validator must never invent shell commands or execute arbitrary command
strings. Destructive hooks require separately reviewed root-owned scripts.

## GitHub workflow acceptance

Required exact-head workflows must actually execute successfully. Static workflow
inspection or local test claims are not production evidence.

For provider-backed acceptance, configure a protected GitHub environment that:

- allows deployment only from the protected `main` branch;
- requires an independent reviewer before secrets are released;
- prevents self-approval by the workflow author;
- retains branch protection and required checks;
- limits provider secrets to the smallest required step scope.

The manual workflow must verify the operator-supplied reviewed commit before any
VPS connection and again before installation, use strict SSH host verification,
remove transient authentication on every exit path, and upload only bounded
non-sensitive evidence.

## Acceptance evidence

Retain:

- exact reviewed commit and root-executed Git blob IDs;
- signed source archive checksum and signature result;
- installer and validator logs with secrets redacted;
- VM image, kernel, filesystems, roles, and addresses;
- pre- and post-reboot boot IDs;
- doctor, service, listener, firewall, and certificate state;
- external web, DNS, TLS, mail, and API results;
- tenant-isolation and authorization results;
- backup, restore, quota, rollback, and provider-snapshot evidence;
- successful exact-head workflow run identifiers;
- approved legal and commercial terms.

## Publication gate

Do not publish or market release `3.4.1` as production-ready while
`RELEASE-MANIFEST.json` reports:

```text
status=deployable-not-publishable
production_publish_allowed=false
```

A reviewed change must remove every blocker, update the status to
`production-ready`, set publication permission to `true`, and pass the release
consistency workflow plus all production acceptance gates.
