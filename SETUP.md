# HostPanel setup

This guide installs the signed `3.4.0-hardened-r5` base release through a
reviewed, commit-addressed installer overlay.

Choose a real, resolvable full commit SHA and substitute it for
`REVIEWED_COMMIT_SHA`. The same value must be used in the download URL and in
`HP_REPO_REF`.

## Security model

The bootstrap does not trust a public key fetched beside the archive it verifies.
It contains the release verification key directly and uses it to authenticate
the signed base archive. It separately verifies each installer overlay file
against the operator-supplied full Git commit object.

The installed root script is derived deterministically from a preserved base
installer. Every expected replacement must match exactly once; otherwise the
hardener exits before any server mutation.

HostPanel changes packages, firewall rules, web and mail services, databases,
DNS, scheduled jobs, and customer data paths. Validate the selected roles on a
fresh disposable server first and keep provider-console access available.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM
- at least 10 GB free on `/`
- root or passwordless sudo access
- a valid panel hostname such as `panel.example.com`
- an administrative IP or CIDR when the installer is not launched from SSH

Ubuntu 26.04 uses distribution-provided PHP 8.5 and Rspamd packages. Automatic
third-party repository setup is disabled on every platform: the installer never
executes a mutable repository bootstrap script as root. Preconfigure a reviewed
repository yourself when additional packages are required, and keep
`HP_MULTI_PHP_REPO=off` and `HP_RSPAMD_REPO=off` during installation.

A full installation selects all roles unless `--role` is supplied:
`control`, `web`, `database`, `mail`, `dns`, `backup`, and `edge`.

## 1. Prepare DNS and administrative access

Create an `A` record for the panel hostname pointing to the server. Add an
`AAAA` record only when IPv6 is configured and protected by the same firewall
policy.

```bash
getent ahosts panel.example.com
```

Determine the source IP or network that must retain panel access. Examples:

```text
192.0.2.10/32
2001:db8:100::/64
```

The installer detects the active SSH port from `SSH_CONNECTION` and `sshd -T`.
It opens those ports before enabling a default-deny firewall and schedules a
five-minute automatic rollback until installation completes successfully.

## 2. Install bootstrap prerequisites

Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl python3
```

Rocky Linux or AlmaLinux:

```bash
sudo dnf install -y ca-certificates curl git openssl python3
```

## 3. Download a reviewed bootstrap

Do not run an unpinned branch URL as root.

```bash
REVIEWED_COMMIT_SHA=REPLACE_WITH_40_HEX_CHARACTERS

sudo curl -fsSL \
  "https://raw.githubusercontent.com/1-vps/hostpanel/${REVIEWED_COMMIT_SHA}/bootstrap-install.sh" \
  -o /root/bootstrap-install.sh
sudo chmod 700 /root/bootstrap-install.sh
sudo bash -n /root/bootstrap-install.sh
```

Confirm that the value contains exactly 40 hexadecimal characters and resolves
in the repository before proceeding.

## 4. Run the preflight

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --check --mta postfix
```

A successful preflight prints:

```text
Preflight passed. No changes were made.
```

Preflight validates the OS, architecture, memory, disk, hostname, ports, roles,
and MTA. It does not install packages, configure repositories, modify the
firewall, build the Python runtime, or start services.

## 5. Fresh installation

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --mta postfix
```

When installation is performed over SSH, the client address is used when
`HP_PANEL_ADMIN_CIDR` is omitted. Without either source, installation fails
closed. `HP_ALLOW_PUBLIC_PANEL=yes` is an explicit override for controlled test
environments and opens the panel port publicly.

A fresh installation creates the `admin` account and prints its generated
password once. The password is passed to the initialization process through
standard input, not command-line arguments.

## 6. Safe reinstall or interrupted-run recovery

Run the reinstall preflight first:

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --reinstall --check --mta postfix
```

Then run the reinstall:

```bash
sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --reinstall --mta postfix
```

Every mutating run creates a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

The directory is mode `0700`; archives and absence manifests are mode `0600`.
It is separate from `/var/backups/hostpanel`, which can contain panel-managed
customer backups. The snapshot includes managed service configuration, package
repository configuration, firewall state, `/etc/fstab`, credentials, runtime
metadata, and relevant application trees. The installer tracks newly installed
packages and removes them on a failed run when possible.

Rollback is best-effort because operating-system package scripts and external
service side effects cannot be made fully transactional. A disposable VM test
and a provider-level snapshot remain mandatory before production upgrades.

## 7. Redis and service validation

When Redis is selected, the installer disables its unauthenticated `default`
ACL user, creates a named `hostpanel` user, writes a root-controlled credential,
and configures Rspamd to authenticate. Required Redis, Dovecot, PostgreSQL,
Apache, Rspamd, Postfix/Exim, and final `hostpanel-doctor` failures stop the
installation instead of being reported as success.

PHP branches are accepted only after required modules are visible to the loaded
CLI runtime. Unavailable optional module packages are recorded in:

```text
/etc/hostpanel/php-skipped-packages
```

## 8. Verify after installation

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
sudo nginx -t
sudo systemctl status hostpanel nginx --no-pager --full
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor
```

Inspect the root-only installer log:

```text
/var/log/hostpanel-install.log
```

Reboot and repeat the service and doctor checks. Verify that the configured SSH
port remains reachable and that firewall rules persist.

## 9. Production acceptance

Before serving customers:

1. install on a disposable systemd VM of the exact target OS;
2. reboot and verify all selected services;
3. test panel login, web, database, DNS, mail, backup, and restore paths selected for the node;
4. confirm quota behavior on the actual customer-data filesystem;
5. replace self-signed certificates with trusted certificates;
6. configure reverse DNS, SPF, DKIM, and DMARC for production mail;
7. retain a tested provider-level recovery path.
