# HostPanel installation

HostPanel provides three installation paths:

- `install-one-line.sh` is the recommended minimal, unattended entry point;
- `auto-install.sh` is the full cloud-init, Terraform, and image-builder engine;
- `quick-install.sh` is the interactive DirectAdmin-style installer.

All paths install version `3.4.1` from reviewed, immutable Git objects.

The recommended one-line entry file is pinned to commit:

```text
d0689bc880a8c43af637622c52b931de87b91d61
```

Its verified Git blob is:

```text
fd3806cd58118e30b0ef2a680bfacdd50f191421
```

The underlying automatic engine remains pinned to commit
`a88be462efa38e479070b89e0a4c90b4b7b202da` and Git blob
`4fa5e025c1516ebaaff260177b572f3253a61aa1`.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root access
- a valid panel FQDN or domain
- an administrative IP or CIDR, or an active SSH connection that can be detected
- a short-lived GitHub token with **Contents: Read-only** access to `1-vps/hostpanel`

Create a provider snapshot and retain console access before installation.

## Recommended one-line Bash file

Obtain the exact `install-one-line.sh` bytes from immutable commit
`d0689bc880a8c43af637622c52b931de87b91d61` through a normal authenticated
checkout or reviewed file transfer. Do not execute a moving `main` branch as
root.

Export the short-lived token in the current user shell:

```bash
export HOSTPANEL_GITHUB_TOKEN='github_pat_REPLACE_ME'
```

Set the domain and run the complete unattended installation with one command:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" bash install-one-line.sh example.com
```

Then clear the user-shell variable:

```bash
unset HOSTPANEL_GITHUB_TOKEN
```

The file converts `example.com` to `panel.example.com`, carries the active SSH
source across `sudo`, installs missing outer prerequisites, downloads the
immutable `auto-install.sh`, verifies its Git blob before execution, and passes
the token on inherited descriptor 3. It never prompts and does not create a
persistent token file.

When the machine already has a valid FQDN from `hostname -f`, omit the domain:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" bash install-one-line.sh
```

To avoid SSH-source detection, provide an explicit administrative network:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env HP_PANEL_ADMIN_CIDR=192.0.2.10/32 bash install-one-line.sh example.com
```

The command automatically:

1. validates the stdin token and keeps it out of URLs and normal arguments;
2. installs missing `curl`, `git`, and CA prerequisites;
3. downloads only the immutable automatic installer commit;
4. verifies the downloaded launcher against its Git blob;
5. validates or detects the panel hostname and administrative CIDR;
6. runs the complete preflight before mutation;
7. installs all roles with Postfix by default;
8. verifies version `3.4.1`, runs the production validator and `hostpanel-doctor`;
9. writes machine-readable status to `/var/lib/hostpanel/auto-install-status.json`.

## Automatic detection

When `HP_PANEL_HOST` is omitted, the installer accepts a valid existing FQDN
from `hostname -f`. Alternatively pass a domain to `install-one-line.sh` or set:

```text
HP_PANEL_DOMAIN=example.com
```

which resolves to `panel.example.com`.

When `HP_PANEL_ADMIN_CIDR` is omitted, the active SSH source is converted to a
single-address `/32` or `/128`. The recommended one-line command explicitly
carries `SSH_CONNECTION` across `sudo`. Cloud-init normally has no SSH source,
so set the CIDR explicitly there. Missing or unsafe values fail closed; the
panel is never made public automatically.

## Provisioning secrets

For cloud-init, systemd credentials, Terraform provisioners, or secret-manager
integrations, invoke `auto-install.sh` directly. It accepts:

```text
HP_GITHUB_TOKEN_FILE=/path/to/root-only-token
HP_GITHUB_TOKEN_FD=3
```

It discovers:

```text
$CREDENTIALS_DIRECTORY/github-token
/run/secrets/hostpanel_github_token
/etc/hostpanel/install-github.token
```

Token files must be root-owned, single-linked regular files with mode `0400` or
`0600`. They are opened with no-follow semantics and rejected if their identity
changes while being read.

## Configuration

The one-line entry file forwards these environment variables to the automatic
engine:

```text
HP_PANEL_HOST=panel.example.com
HP_PANEL_DOMAIN=example.com
HP_PANEL_ADMIN_CIDR=192.0.2.10/32
HP_MTA=postfix
HP_ROLES="control web database mail dns backup edge"
HP_REINSTALL=yes
HP_CHECK_ONLY=yes
HP_POST_INSTALL_CHECK=no
```

Omitting `HP_ROLES` installs all roles. Supported roles are `control`, `web`,
`database`, `mail`, `dns`, `backup`, and `edge`.

`HP_CHECK_ONLY=yes` runs the full preflight without changing the server.
`HP_REINSTALL=yes` is required for an explicit replacement of an existing
installation.

## Idempotence and status

A second run against a healthy installed `3.4.1` verifies the validator and
doctor and exits successfully instead of reinstalling. A different installed
version fails unless `HP_REINSTALL=yes` is explicit.

Concurrent runs are rejected by a root-only lock under:

```text
/run/hostpanel-auto-install/
```

Read automation state with:

```bash
sudo cat /var/lib/hostpanel/auto-install-status.json
```

## Cloud-init

Use [`examples/cloud-init-hostpanel.yaml`](examples/cloud-init-hostpanel.yaml).
The example expects the provider or secret manager to create
`/run/secrets/hostpanel_github_token`; it deliberately does not embed a token in
cloud-init metadata.

## Interactive installation

For a guided installation, use `quick-install.sh`. It prompts for the token,
hostname, CIDR, and confirmation while retaining the same preflight and
Git-object verification.

## Verify

```bash
cat /opt/hostpanel/VERSION
sudo cat /var/lib/hostpanel/auto-install-status.json
sudo /opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor --quiet
sudo bash /root/validate-production-vm.sh --check
sudo systemctl status hostpanel nginx hostpanel-update.timer --no-pager --full
```

Installer log:

```text
/var/log/hostpanel-install.log
```
