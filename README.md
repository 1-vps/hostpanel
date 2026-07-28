# HostPanel

HostPanel is a multi-tenant Linux hosting control panel for web, DNS, mail,
databases, backups, certificates, firewall policy, monitoring, and infrastructure
operations.

**Current working release:** `3.4.0-hardened-r6`  
**Signed base release:** `3.4.0-hardened-r5`  
**Validated installer overlay:** `65d7f54b4c08edef65b2c13389b0a036c6a56b5b`  
**License:** MIT

> HostPanel changes operating-system packages, service configuration, firewall
> rules, databases, mail, DNS, scheduled jobs, and customer data paths. Use a
> fresh disposable server for initial validation and keep provider-console access
> available.

## Installation trust model

Do not execute an unpinned `main` branch script as root.

The bootstrap has two independent verification layers:

1. an embedded long-lived release public key verifies the signed
   `3.4.0-hardened-r5` source archive;
2. the operator-supplied full Git commit SHA authenticates the installer overlay
   that derives and installs `3.4.0-hardened-r6`.

Every overlay file is checked against its Git object before use. The preserved
base installer is also checked by its expected Git blob ID before the generated
root installer can run.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root or passwordless sudo access
- a valid panel hostname
- an administrative IP or CIDR for `HP_PANEL_ADMIN_CIDR` when installation is
  not performed over SSH

Automatic third-party repository bootstrap is disabled. Preconfigure any
reviewed external repository yourself, then run with `HP_MULTI_PHP_REPO=off`
and `HP_RSPAMD_REPO=off`. Ubuntu 26.04 uses its distribution PHP 8.5 and Rspamd
packages.

## Download the pinned bootstrap

The commands below use the installer commit that passed deterministic generation,
ShellCheck, all supported-OS preflights, signed-archive verification, and the
Ubuntu 26.04/Python 3.14 locked-runtime test.

```bash
REVIEWED_COMMIT_SHA=65d7f54b4c08edef65b2c13389b0a036c6a56b5b

sudo curl -fsSL \
  "https://raw.githubusercontent.com/1-vps/hostpanel/${REVIEWED_COMMIT_SHA}/bootstrap-install.sh" \
  -o /root/bootstrap-install.sh
sudo chmod 700 /root/bootstrap-install.sh
sudo bash -n /root/bootstrap-install.sh
```

## Preflight

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --check --mta postfix
```

The preflight validates inputs and host capacity but does not install packages,
configure repositories, change the firewall, or start services.

## Fresh installation

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --mta postfix
```

Public panel exposure is fail-closed. When no administrative source can be
detected, installation stops unless `HP_PANEL_ADMIN_CIDR` is supplied. Setting
`HP_ALLOW_PUBLIC_PANEL=yes` explicitly accepts public exposure and should be
reserved for controlled testing.

## Safe reinstall

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --reinstall --mta postfix
```

A mutating run creates a root-owned `0700` snapshot directory at
`/var/backups/hostpanel-install/`. Snapshot archives and their absence manifests
remain root-only and are never placed under the panel-owned customer backup tree.
The installer also tracks newly installed packages, managed paths and a timed
firewall rollback. Rollback is best-effort; always validate on a disposable VM
before upgrading a production server.

## Verify

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
sudo nginx -t
sudo systemctl status hostpanel nginx --no-pager --full
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor
```

Expected installed version:

```text
3.4.0-hardened-r6
```

Installer log:

```text
/var/log/hostpanel-install.log
```

## Production validation required

Before serving customers:

1. complete a full installation on a disposable systemd VM of the target OS;
2. reboot and run `hostpanel-doctor` without ignored failures;
3. test every selected role and verify all required services;
4. create a backup and perform a restore test;
5. verify firewall persistence and reconnect over the configured SSH port;
6. configure real TLS, DNS, reverse DNS, SPF, DKIM, and DMARC as applicable.

## Maintained documentation

- [`SETUP.md`](SETUP.md) — installation, reinstall, verification, and recovery
- [`CONFIGURATION.md`](CONFIGURATION.md)
- [`SECURITY.md`](SECURITY.md)
- [`FIREWALL.md`](FIREWALL.md)
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)
