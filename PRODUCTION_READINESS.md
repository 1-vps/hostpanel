# HostPanel production readiness

<!-- {{HOSTPANEL_RELEASE_VERSION}}=3.4.1 -->
<!-- {{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5 -->
<!-- {{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable -->
<!-- {{HOSTPANEL_PUBLICATION_ALLOWED}}=false -->

This checklist applies to deployable HostPanel release **3.4.1**, derived from
signed base **3.4.0-hardened-r5**. It does not itself authorize production
publication. The authoritative state and current blockers are defined in
[`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json).

The only documented installation entry point is the immutable automatic engine
from [`SETUP.md`](SETUP.md):

```text
auto-install.sh commit  1a86d380e7ebab287c767d183013b599cb116f7f
auto-install.sh blob    db23963e101b9194994da2ff8077b40a6b1cb99c
product commit          755dcd5e47b7c82404b267e8df4dec27626fe341
installed version       3.4.1
```

A release is not production-ready until every selected role and recovery path
passes on the exact reviewed commit, operating system, and infrastructure intended
for deployment, all hosted gates are green, and all legal terms are approved.

## Source and authentication prerequisites

This repository is private. Do not use anonymous `raw.githubusercontent.com`
commands, moving `main` references, or place a GitHub token in a URL or normal
command-line argument.

Follow [`SETUP.md`](SETUP.md) to obtain and verify the exact automatic-installer
Git object. Supply a short-lived **Contents: Read-only** GitHub token through an
inherited descriptor or a root-owned, single-linked mode `0400`/`0600` secret
file. Remove local authentication material on both success and failure.

The immutable installer chain verifies the delegated launcher/product objects
before root execution. Record the exact commits and Git blob IDs used for the
acceptance run together with the signed-source checksum and signature result.

## Prepare a disposable VM

Use a fresh systemd VM of the exact target operating system. Keep provider-console
access available and create a provider-level snapshot before installation. Do not
start with a production server or a host containing customer data.

Record the operating-system image and kernel, public/private addresses, SSH port
and host-key fingerprint, panel hostname and administrative CIDR, customer-data
filesystem, selected roles, provider snapshot identifier, and the exact reviewed
installer/product object identities.

Supported systems and resource minimums come from `RELEASE-MANIFEST.json` and are
validated by `tools/validate_release_manifest.py`.

## Install from the immutable automatic engine

Run the reviewed `auto-install.sh` path from [`SETUP.md`](SETUP.md). The normal
installation path does not accept a moving repository ref. Complete non-mutating
preflight first, then installation through the pinned object chain with the same
hostname, roles, and network policy.

Preserve `/var/log/hostpanel-install.log` and the root-owned installer snapshot.
Confirm:

```bash
test "$(tr -d '[:space:]' < /opt/hostpanel/VERSION)" = 3.4.1
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor --quiet
```

## Initial validation

Run the reviewed production validator from the installed/reviewed chain:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

Resolve every failure before continuing. Warnings require explicit review even
when they do not automatically fail validation. An installed OpenLiteSpeed binary
or `lsws.service` must have an active and valid service state.

## Verify reboot persistence

Record the current boot ID only after the initial checks pass, prepare the reboot
marker with the validator, reboot, reconnect over the configured SSH port, and run
`--post-reboot` with the same expected version, hostname, and public IP. The
post-reboot mode must verify a changed kernel boot ID and persistent service,
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

On a disposable VM with a confirmed provider snapshot, create representative
application/database/mail/DNS/certificate state, back it up, inject a reviewed
failure, restore it, verify ownership/permissions/quotas and external behavior,
and exercise both installer rollback and provider-snapshot recovery. Destructive
hooks must be separately reviewed root-owned scripts; the validator must never
invent shell commands or execute arbitrary command strings.

## Hosted acceptance

Every required exact-head Buildkite job must actually execute successfully.
Static pipeline inspection or local test claims are not production evidence. The
hardened Buildkite pipeline verifies repository regressions, release metadata,
production-validator contracts, installer static checks, and supported-OS
preflight on pull-request heads. QEMU VM acceptance runs for eligible `main`
builds after those gates.

Provider-backed VPS acceptance remains a separate manual production gate. Its
protected GitHub environment must allow deployment only from protected `main`,
require an independent reviewer, prevent self-approval, retain branch protection,
and scope provider secrets to the smallest required steps.

## Acceptance evidence

Retain:

- exact reviewed commit and root-executed Git blob IDs;
- signed source archive checksum and signature result;
- successful exact-head Buildkite build and job identifiers;
- installer and validator logs with secrets redacted;
- VM image, kernel, filesystems, roles, and addresses;
- pre- and post-reboot boot IDs;
- doctor, service, listener, firewall, and certificate state;
- external web, DNS, TLS, mail, and API results;
- tenant-isolation and authorization results;
- backup, restore, quota, rollback, and provider-snapshot evidence;
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
consistency checks plus all production acceptance gates.
