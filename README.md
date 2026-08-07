<p align="center">
  <img src="app/static/hostpanel-logo.svg" alt="HostPanel" width="460">
</p>

# HostPanel

<!-- {{HOSTPANEL_RELEASE_VERSION}}=3.4.1 -->
<!-- {{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5 -->
<!-- {{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable -->
<!-- {{HOSTPANEL_PUBLICATION_ALLOWED}}=false -->

HostPanel is a multi-tenant Linux hosting control panel for web, DNS, mail,
databases, backups, certificates, firewall policy, monitoring, and infrastructure
operations.

## Authoritative release state

The single machine-readable source of truth is
[`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json).

- Current deployable release: **3.4.1**
- Authenticated signed base: **3.4.0-hardened-r5**
- Release channel: **candidate**
- Production publication: **blocked**

A deployable build is not the same as an approved production publication. The
manifest lists every current publication blocker. Buildkite's `release-metadata`
gate runs `tools/validate_release_manifest.py` and the matching regression tests
against the exact checked-out commit.

The current `LICENSE` remains a draft with unresolved legal and commercial
placeholders. It must not be treated as approved commercial terms.

## Trust model

HostPanel changes operating-system packages, service configuration, firewall
rules, databases, mail, DNS, scheduled jobs, and customer-data paths. Never run
a moving branch script as root.

Installation uses two independent verification layers:

1. the embedded long-lived release public key verifies the signed
   `3.4.0-hardened-r5` base-source archive;
2. the immutable `auto-install.sh` chain described in [`SETUP.md`](SETUP.md)
   verifies the reviewed installer, delegated launcher, and product Git objects
   before root execution.

The installed `/opt/hostpanel/VERSION` must contain `3.4.1`. The signed-base label
identifies the authenticated starting archive, not the final installed release.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04
- Debian 12 or 13
- Rocky Linux 9 or 10
- AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GiB RAM and 10 GiB free on `/`
- root or passwordless sudo access
- a valid panel hostname
- an administrative IP or CIDR when installation is not performed over SSH
- a short-lived GitHub token with Contents: Read-only access to this private repository

Automatic third-party repository bootstrap is disabled. Preconfigure reviewed
external repositories yourself and keep `HP_MULTI_PHP_REPO=off` and
`HP_RSPAMD_REPO=off` unless the repository has been explicitly reviewed.

## Secure installation

Anonymous `raw.githubusercontent.com` commands do not work for this private
repository. Never place a GitHub token in a URL or normal command-line argument.

Follow [`SETUP.md`](SETUP.md) exactly. Obtain the reviewed `auto-install.sh`
object, verify its documented Git blob, and supply the short-lived repository
token through the inherited descriptor path or the documented root-owned secret
file. The normal automatic-install path does not accept a moving repository ref.

Use `HP_CHECK_ONLY=yes` for non-mutating preflight and `HP_REINSTALL=yes` only
for an explicit replacement of an existing installation. Panel exposure fails
closed; supply `HP_PANEL_ADMIN_CIDR` when no administrative source can be safely
detected. `HP_ALLOW_PUBLIC_PANEL=yes` is an explicit controlled-test override.

## Reinstall, rollback, and verification

Every mutating run creates a root-owned safety snapshot below
`/var/backups/hostpanel-install/`. Rollback is best effort because package scripts
and external service side effects are not fully transactional. Keep a provider
snapshot and console access.

After installation, verify at minimum:

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
nginx -t
systemctl status hostpanel nginx --no-pager --full
/opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor
bash /root/validate-production-vm.sh --check
```

Expected installed application version:

```text
3.4.1
```

Installer log:

```text
/var/log/hostpanel-install.log
```

## Production acceptance

Before customer use, complete the exact checklist in
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md), including fresh-VM
installation, external web/DNS/mail checks, backup and restore, quota enforcement,
firewall persistence, verified reboot, recovery testing, hosted workflow evidence,
and legal approval.

Buildkite validates pull-request heads through the hardened disposable-worker
pipeline. QEMU VM acceptance runs for eligible `main` builds after the supported
OS and core validation gates. Provider-backed VPS acceptance remains a separate,
manual, protected-environment production gate.

## Maintained documentation

- [`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json) — authoritative release state
- [`SETUP.md`](SETUP.md) — authenticated private-repository installation
- [`CONFIGURATION.md`](CONFIGURATION.md) — supported installation controls
- [`SECURITY.md`](SECURITY.md) — security policy and operational boundaries
- [`FIREWALL.md`](FIREWALL.md) — firewall behavior
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) — release acceptance gates
- [`RELEASE-PROCESS.md`](RELEASE-PROCESS.md) — publication process
- [`UPDATES.md`](UPDATES.md) — update and rollback behavior
- [`INDEX.md`](INDEX.md) — current documentation map and historical-audit notice
