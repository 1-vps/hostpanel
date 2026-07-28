# HostPanel setup

This is the maintained setup and reinstall guide for
`3.4.0-hardened-r6`.

The reviewed installer commit is:

```text
2dee7b6326c6158392aa48693634fcabea171ba1
```

The bootstrap verifies the embedded signed `3.4.0-hardened-r5` source archive,
applies the reviewed r6 repair chain from the same pinned commit, and installs
`3.4.0-hardened-r6`.

HostPanel changes operating-system packages, web and mail services, firewall
rules, databases, DNS, scheduled jobs, and customer data paths. Use a fresh
server when possible and keep provider-console access available.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04, Debian 12 or 13, Rocky Linux 9 or 10, or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM
- at least 10 GB free on `/`
- root or passwordless sudo access
- a valid panel hostname, for example `panel.example.com`

Ubuntu 26.04 uses the distribution-provided PHP 8.5 and Rspamd packages.
The installer deliberately skips the Ondrej PHP PPA and the upstream Rspamd
APT repository on this release because neither currently publishes a
`resolute` suite. Multi-PHP availability is therefore limited to branches
published by Ubuntu unless a separately reviewed repository strategy is added.

A full installation selects all roles unless `--role` is supplied:
`control`, `web`, `database`, `mail`, `dns`, `backup`, and `edge`.

## 1. Prepare DNS

Create an `A` record for the panel hostname pointing to the server. Add an
`AAAA` record only when IPv6 is configured and protected by the same firewall
policy.

```bash
getent ahosts panel.example.com
```

The hostname must resolve to the intended server before production use.

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

## 3. Download the pinned bootstrap

Do not run an unpinned branch URL as root.

```bash
sudo curl -fsSL \
  https://raw.githubusercontent.com/1-vps/hostpanel/2dee7b6326c6158392aa48693634fcabea171ba1/bootstrap-install.sh \
  -o /root/bootstrap-install.sh

sudo chmod 700 /root/bootstrap-install.sh
sudo bash -n /root/bootstrap-install.sh
```

`HP_REPO_REF` must be the same full commit SHA used in the download URL.

## 4. Run the preflight

Replace `panel.example.com` with the real hostname. Choose `postfix` or `exim`
deliberately; this example uses Exim.

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --check --mta exim
```

A successful preflight prints:

```text
Preflight passed. No changes were made.
```

The preflight checks the operating system, architecture, memory, disk space,
hostname, selected roles, MTA, environment values, and required ports. It does
not install packages or modify the server.

## 5. Fresh installation

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --mta exim
```

A fresh installation creates:

```text
Username: admin
```

The generated administrator password is printed once at the end of the first
successful installation. Save it immediately. Only its password hash is kept,
so the plaintext cannot be recovered later.

## 6. Safe reinstall or interrupted-run recovery

Use `--reinstall` for an existing HostPanel installation, an interrupted run,
or a newer reviewed release.

Run the reinstall preflight first:

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --reinstall --check --mta exim
```

Then run the reinstall:

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --reinstall --mta exim
```

Reinstall mode creates a root-only safety snapshot under
`/var/backups/hostpanel/install/`, reconciles supported package-manager and
PostgreSQL state, replaces the application and versioned runtime, performs an
authenticated readiness check, and attempts rollback if a later stage fails.

It preserves:

- administrator accounts and password hashes;
- panel, master, node, and readiness secrets;
- the PostgreSQL control-plane credential;
- the MariaDB root credential;
- roles and MTA selection unless explicitly changed;
- customer websites, mailboxes, databases, backups, and plugins.

A reinstall does not print or reset the administrator password.

## 7. Open the panel

Open the configured hostname:

```text
https://panel.example.com:2222/
```

Do not use the server IP address. Trusted-host protection intentionally rejects
unconfigured hosts with:

```text
Invalid host header
```

The first visit warns about the self-signed certificate. Replace it before
production use.

Verify the hostname path from the server:

```bash
grep -E '^(HP_EXTERNAL_URL|HP_TRUSTED_HOSTS)=' \
  /opt/hostpanel/config.env

curl -sS -D- -o /dev/null \
  -H 'Host: panel.example.com' \
  http://127.0.0.1:12722/

curl -sk -D- -o /dev/null \
  --resolve panel.example.com:2222:127.0.0.1 \
  https://panel.example.com:2222/
```

A healthy unauthenticated panel returns `303 See Other` and
`location: /login`.

## 8. Verify the installation

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
sudo nginx -t
sudo systemctl status hostpanel nginx --no-pager --full
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor
```

Expected version:

```text
3.4.0-hardened-r6
```

Installer log:

```text
/var/log/hostpanel-install.log
```

Install state:

```text
/etc/hostpanel/install-state
```

## 9. Required post-install work

### Restrict panel access

When port `2222` is open to the internet, restrict it to an administrative IP or
trusted VPN. Keep provider-console access while changing firewall rules.

```bash
sudo ufw delete allow 2222/tcp
sudo ufw allow from YOUR.PUBLIC.IP to any port 2222 proto tcp
sudo ufw status numbered
```

Apply equivalent IPv6 policy when public IPv6 is enabled.

### Enable filesystem quotas

When the installer adds quota mount options, reboot first:

```bash
sudo reboot
```

After reconnecting:

```bash
sudo quotacheck -cugm /
sudo quotaon /
sudo quotaon -p /
```

### Replace the self-signed certificate

Install a trusted certificate for the panel hostname and test automatic renewal
before production exposure.

### Create and test backups

Run **Backups → Back up now** after the first login. Keep production backups on
separate storage and perform a restore test.

### Configure mail authentication

For each mail domain, generate and publish DKIM, and verify forward DNS, reverse
DNS, SPF, DKIM, and DMARC. The doctor may warn about DKIM until a real mail
domain and selector exist.

### Check OpenLiteSpeed when selected

When the doctor reports `lsws` inactive:

```bash
sudo systemctl status lsws --no-pager --full
sudo journalctl -u lsws -n 100 --no-pager
sudo /usr/local/lsws/bin/lswsctrl start
sudo ss -ltnp | grep -E ':(8088|7080)[[:space:]]'
```

OpenLiteSpeed should use private backend port `127.0.0.1:8088`; WebAdmin should
remain local on port `7080`.

## Role examples

Control, web, database, and backup node:

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh \
    --role control,web,database,backup
```

Exim mail and DNS node:

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=mail.example.com \
  bash /root/bootstrap-install.sh \
    --role mail,dns --mta exim
```

Use the same role list with `--check` before the real run.

## Troubleshooting

### Installer failure

```bash
sudo tail -n 200 /var/log/hostpanel-install.log
sudo cat /etc/hostpanel/install-state 2>/dev/null || true
sudo systemctl status hostpanel --no-pager --full
sudo journalctl -u hostpanel -n 120 --no-pager
```

Resume with the same reviewed commit and `--reinstall`. Do not delete
`/opt/hostpanel`, customer data, or reinstall snapshots as the first recovery
step.

### Invalid host header

Use the exact configured hostname and port. Do not add `*` to trusted hosts to
make IP-based access work.

```bash
grep -E '^(HP_EXTERNAL_URL|HP_TRUSTED_HOSTS)=' \
  /opt/hostpanel/config.env
```

### Administrator password unavailable

The original plaintext password cannot be read from the database or logs after
the first-install output is gone. Reinstall preserves the password hash and does
not generate a replacement. Use the supported account-recovery path; do not edit
password hashes manually.

## Security

- Never publish `/opt/hostpanel/config.env`, `/root/.my.cnf`, readiness tokens,
  private keys, database URLs, or customer data.
- Keep panel access restricted whenever practical.
- Keep the bootstrap URL and `HP_REPO_REF` pinned to the same reviewed commit.
- Run `hostpanel-doctor` after configuration changes and before production use.

Maintained supporting documents:

- [`README.md`](README.md)
- [`RELEASE-NOTES-v3.4.0-hardened-r6.md`](RELEASE-NOTES-v3.4.0-hardened-r6.md)
- [`CONFIGURATION.md`](CONFIGURATION.md)
- [`SECURITY.md`](SECURITY.md)
- [`FIREWALL.md`](FIREWALL.md)
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)
