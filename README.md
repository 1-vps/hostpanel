# HostPanel

HostPanel is a multi-tenant Linux hosting control panel for web, DNS, mail,
databases, backups, certificates, firewall policy, monitoring, and infrastructure
operations.

**Current working release label:** `3.4.0-hardened-r6`  
**Signed base release:** `3.4.0-hardened-r5`  
**Installed `/opt/hostpanel/VERSION`:** `3.4.0`  
**Reviewed installer overlay:** `9c38d0095563ea33efd14124babfd29556c0da46`  
**Merged implementation:** `6a2b8f76ece798408ef7b04586e73be8c3041750`  
**License:** MIT

> HostPanel changes operating-system packages, service configuration, firewall
> rules, databases, mail, DNS, scheduled jobs, and customer data paths. Validate
> it on a fresh disposable server and keep provider-console access available.

## Trust model

Do not execute an unpinned branch script as root.

The installer uses two independent verification layers:

1. an embedded long-lived release public key verifies the signed
   `3.4.0-hardened-r5` source archive;
2. the operator-supplied full Git commit SHA authenticates every installer
   overlay object that derives the `3.4.0-hardened-r6` working release.

The signed application writes `3.4.0` to `/opt/hostpanel/VERSION`. The
`hardened-r5` and `hardened-r6` identifiers are packaging and installer-overlay
revision labels, not installed application versions.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root or passwordless sudo access
- a valid panel hostname
- an administrative IP or CIDR for `HP_PANEL_ADMIN_CIDR` when installation is
  not performed over SSH
- a short-lived GitHub token with **Contents: Read-only** access to this private repository

Automatic third-party repository bootstrap is disabled. Preconfigure any
reviewed external repository yourself, then use `HP_MULTI_PHP_REPO=off` and
`HP_RSPAMD_REPO=off`.

## Secure private-repository installation

Anonymous `raw.githubusercontent.com` commands do not work for this private
repository. Do not put a GitHub token in a URL or command-line argument. The full
root-shell procedure, including token cleanup, is documented in
[`SETUP.md`](SETUP.md).

The procedure:

1. prompts without echo for a short-lived read-only token;
2. downloads `bootstrap-install.sh` and `tools/validate-production-vm.sh` through
   the GitHub Contents API from exactly
   `9c38d0095563ea33efd14124babfd29556c0da46`;
3. provides transient Git authentication only while the bootstrap fetches that
   reviewed commit;
4. removes the token file, plain token, encoded header, and Git authentication
   variables after installation.

After the pinned files and transient Git authentication are prepared, run:

```bash
HP_REPO_REF=9c38d0095563ea33efd14124babfd29556c0da46 \
HP_PANEL_HOST=panel.example.com \
HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
HP_MULTI_PHP_REPO=off \
HP_RSPAMD_REPO=off \
bash /root/bootstrap-install.sh --check --mta postfix

HP_REPO_REF=9c38d0095563ea33efd14124babfd29556c0da46 \
HP_PANEL_HOST=panel.example.com \
HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
HP_MULTI_PHP_REPO=off \
HP_RSPAMD_REPO=off \
bash /root/bootstrap-install.sh --mta postfix
```

Public panel exposure is fail-closed. When no administrative source can be
detected, installation stops unless `HP_PANEL_ADMIN_CIDR` is supplied.
`HP_ALLOW_PUBLIC_PANEL=yes` is an explicit override for controlled testing only.

## Reinstall and rollback

Use `--reinstall --check` before a mutating reinstall. Every mutating run creates
a root-owned `0700` safety snapshot under `/var/backups/hostpanel-install/`.
Rollback is best-effort because operating-system package scripts and external
service side effects are not fully transactional. Keep a provider snapshot and
console access for production changes.

## Verify

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
3.4.0
```

Installer log:

```text
/var/log/hostpanel-install.log
```

## Least-privilege QEMU acceptance

The repository includes
[`.github/workflows/qemu-vm-acceptance.yml`](.github/workflows/qemu-vm-acceptance.yml).
It boots a checksum-pinned Ubuntu 24.04 cloud image with an ephemeral SSH key,
installs the reviewed commit, validates services, performs a real systemd reboot,
and uploads bounded non-sensitive evidence.

The workflow has read-only repository permissions. GitHub's per-run token is
available only to the installation step, converted to a transient Git header,
removed from runner variables before QEMU starts, and deleted from the guest
environment after the reviewed commit is fetched. It is never included in an
artifact. No repository-defined secret is required for QEMU acceptance.

Pull requests from forks do not execute the full VM job because untrusted fork
code must not receive the same runner trust boundary.

## Provider-backed acceptance

[`.github/workflows/vps-acceptance.yml`](.github/workflows/vps-acceptance.yml) is
manual, environment-gated, and destructive. It checks out the exact reviewed
commit, verifies `HEAD` before any VPS connection and before installation,
requires a confirmed provider snapshot, uses strict SSH host verification, and
removes transient Git authentication on all exit paths.

This complements QEMU by testing public networking, trusted TLS, DNS delegation,
mail deliverability, reverse DNS, backup/restore, quota enforcement, and recovery
on the intended infrastructure.

## Production validation required

Before serving customers:

1. complete a full installation on a disposable systemd VM of the target OS;
2. run the production validator before and after a verified reboot;
3. test every selected role and required service externally;
4. create a backup and perform a restore test;
5. verify firewall persistence and reconnect over the configured SSH port;
6. configure trusted TLS, DNS, reverse DNS, SPF, DKIM, and DMARC as applicable.

## Maintained documentation

- [`SETUP.md`](SETUP.md) — authenticated private-repository installation and recovery
- [`CONFIGURATION.md`](CONFIGURATION.md)
- [`SECURITY.md`](SECURITY.md)
- [`FIREWALL.md`](FIREWALL.md)
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)
