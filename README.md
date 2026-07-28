# HostPanel

HostPanel is a multi-tenant Linux hosting control panel for web, DNS, mail,
databases, backups, certificates, firewall policy, monitoring, and infrastructure
operations.

**Current working release:** `3.4.0-hardened-r6`  
**Validated installer commit:** `2dee7b6326c6158392aa48693634fcabea171ba1`  
**License:** MIT

> HostPanel changes operating-system packages, service configuration, firewall
> rules, databases, mail, DNS, scheduled jobs, and customer data paths. Use a
> fresh server when possible and keep provider-console access available.

## Setup

Use the single maintained installation guide:

- [`SETUP.md`](SETUP.md)

Do not run an unpinned `main` branch script as root. Download
`bootstrap-install.sh` and set `HP_REPO_REF` to the same reviewed full commit
SHA.

### Quick preflight

Replace `panel.example.com` with the real panel hostname:

```bash
sudo curl -fsSL \
  https://raw.githubusercontent.com/1-vps/hostpanel/2dee7b6326c6158392aa48693634fcabea171ba1/bootstrap-install.sh \
  -o /root/bootstrap-install.sh
sudo chmod 700 /root/bootstrap-install.sh
sudo bash -n /root/bootstrap-install.sh

sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --check --mta exim
```

### Fresh installation

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --mta exim
```

### Existing or interrupted installation

```bash
sudo env \
  HP_REPO_REF=2dee7b6326c6158392aa48693634fcabea171ba1 \
  HP_PANEL_HOST=panel.example.com \
  bash /root/bootstrap-install.sh --reinstall --mta exim
```

Reinstall mode preserves administrator accounts and password hashes, secrets,
customer data, the PostgreSQL control-plane credential, and the MariaDB root
credential. It does not print or reset the administrator password.

## Panel access

Open the configured hostname, normally:

```text
https://panel.example.com:2222/
```

Do not use the server IP address. Trusted-host protection intentionally rejects
unconfigured hosts with `Invalid host header`.

A fresh installation creates the `admin` account and prints its generated
password once. Save it immediately; only the password hash is retained.

## Verify

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

## Required production work

Before serving customers:

1. restrict panel port `2222` to an administrative IP or VPN;
2. replace the self-signed panel certificate;
3. reboot and enable quotas when requested by the installer;
4. create a backup and perform a restore test;
5. configure DNS, reverse DNS, SPF, DKIM, and DMARC for mail domains;
6. resolve all relevant `hostpanel-doctor` failures.

## Maintained documentation

- [`SETUP.md`](SETUP.md) — installation, reinstall, verification, and recovery
- [`RELEASE-NOTES-v3.4.0-hardened-r6.md`](RELEASE-NOTES-v3.4.0-hardened-r6.md)
- [`CONFIGURATION.md`](CONFIGURATION.md)
- [`SECURITY.md`](SECURITY.md)
- [`FIREWALL.md`](FIREWALL.md)
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)

Historical release evidence remains separate from the working setup path.
