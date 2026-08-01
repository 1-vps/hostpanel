# HostPanel production readiness

This checklist applies to the `3.4.0-hardened-r6` working release after the
installer pipeline has passed. It does not replace validation on a real systemd
VM using the intended kernel, firewall, storage, DNS, and mail environment.

**Reviewed installer overlay:**

```text
9c38d0095563ea33efd14124babfd29556c0da46
```

**Installed application version:**

```text
3.4.0
```

## Source and authentication prerequisites

This repository is private. Do not use anonymous `raw.githubusercontent.com`
commands and do not place a GitHub token in a URL or command-line argument.
Follow [`SETUP.md`](SETUP.md) to:

- prompt without echo for a short-lived Contents:Read token;
- fetch bootstrap and validator through the GitHub Contents API from the exact
  reviewed commit;
- verify Git blob IDs before either file is executed as root;
- provide transient Git authentication while the bootstrap fetches the reviewed
  overlay;
- remove local and remote authentication state on success and failure.

Required reviewed Git blobs:

```text
bootstrap-install.sh                  eae493681ce5eecd5ea61491f8e08e1f40938e08
tools/validate-production-vm.sh      2672271aacc0d85013765b3a7887fdec95518643
```

## Prepare a disposable VM

Use a fresh VM of the exact target operating system. Keep provider-console access
available and create a provider-level snapshot before installation. Do not begin
with a production server or a host containing customer data.

Record:

- operating-system image and kernel;
- public and private addresses;
- SSH port and strict host-key fingerprint;
- panel hostname and administrative CIDR;
- customer-data filesystem;
- selected HostPanel roles;
- provider snapshot identifier;
- reviewed Git commit and verified blob IDs.

## Install from the pinned commit

Run the authenticated driver from [`SETUP.md`](SETUP.md). It must complete both
the non-mutating preflight and installation from:

```text
HP_REPO_REF=9c38d0095563ea33efd14124babfd29556c0da46
```

Preserve `/var/log/hostpanel-install.log` and the snapshot path printed by the
installer. Confirm:

```bash
test "$(tr -d '[:space:]' < /opt/hostpanel/VERSION)" = 3.4.0
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor --quiet
```

## Initial validation

The blob-verified validator is downloaded before installation by the setup
driver. Run:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

Resolve every failure before continuing. Warnings require explicit review even
when they do not make the validator fail automatically. In particular, an
installed OpenLiteSpeed binary or `lsws.service` must have an active and valid
service state; it must not be accepted as an unexplained post-reboot warning.

## Verify reboot persistence

Record the current boot ID only after the initial checks pass:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --prepare-reboot

sudo reboot
```

Reconnect over the configured SSH port and run:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --post-reboot
```

The post-reboot mode requires a changed kernel boot ID before it accepts the
reboot check.

## End-to-end role tests

The validator confirms local state. Production acceptance also requires tests
from a separate network:

- panel login and session handling over trusted TLS;
- customer website creation, TLS issuance, PHP execution, and log access;
- database creation, authentication, backup, and restore;
- authoritative DNS delegation and external query resolution;
- inbound and outbound mail, submission, IMAP, SPF, DKIM, DMARC, and reverse DNS;
- scheduled backup completion and restoration into a disposable account;
- quota enforcement on the actual customer-data filesystem;
- SSH reconnection after firewall reload and reboot;
- public IPv4 and IPv6 behavior where applicable.

Do not treat local port checks as proof of external DNS delegation, trusted TLS,
or mail deliverability.

## Destructive recovery tests

Restore and failure-injection tests must use a disposable VM, a confirmed
provider snapshot, and separately reviewed root-owned scripts. The validator
will not invent shell commands or execute arbitrary command strings. Review its
`--help` output for accepted hook paths and safety gates.

The manual `vps-acceptance` workflow is suitable only when its protected GitHub
environment is configured. Before storing any provider secret in that
environment, configure all of the following controls:

- allow deployments from the protected `main` branch only;
- require at least one independent reviewer before environment secrets are
  released to a job;
- prevent the workflow author from approving their own protected deployment;
- retain branch protection on `main`, including required checks and review;
- periodically verify these settings because repository code cannot enforce or
  inspect environment protection rules by itself.

Dispatch the workflow from `main` and enter the exact integrated 40-character
commit SHA in `reviewed_commit_sha`. The selected commit—not a historical
hard-coded release commit—must contain every installer, UI, localization, and
validation change intended for release.

The workflow must:

- require the exact destructive confirmation phrase;
- require snapshot confirmation;
- reject dispatches whose workflow ref is not `refs/heads/main`;
- check out and verify the operator-supplied reviewed commit rather than the
  dispatch ref;
- use strict SSH host verification;
- expose the root password and known-host material only to the steps that need
  them, never through job-wide `env`;
- clean transient runner and remote Git authentication;
- fail when external DNS, trusted HTTPS, or required public listeners do not
  pass, rather than recording informational probe output;
- upload only bounded non-sensitive evidence.

## Acceptance evidence

Retain:

- installer and validator logs with secrets redacted;
- exact Git commit and root-executed blob IDs;
- signed source archive checksum;
- VM image, kernel, filesystem, and addresses;
- pre- and post-reboot boot IDs;
- doctor, service, listener, and firewall status;
- external web, DNS, TLS, and mail results;
- backup, restore, quota, and rollback evidence;
- provider snapshot identifiers.

A release is not production-ready until every selected role and recovery path
passes on the exact operating system and infrastructure intended for deployment.
