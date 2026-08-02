# HostPanel installation

`auto-install.sh` is the only documented HostPanel installation entry point. It
is the full cloud-init, Terraform, image-builder, and unattended VPS engine.

It installs HostPanel version `3.4.1` from reviewed, immutable Git objects. The
installer is pinned to commit:

```text
a88be462efa38e479070b89e0a4c90b4b7b202da
```

Its verified Git blob is:

```text
4fa5e025c1516ebaaff260177b572f3253a61aa1
```

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root access
- a valid panel FQDN or domain
- an administrative IP or CIDR, or an active SSH connection that can be detected
- a short-lived GitHub token with **Contents: Read-only** access to `1-vps/hostpanel`

Create a provider snapshot and retain console access before installation.

## Obtain the installer

Obtain the exact `auto-install.sh` bytes from immutable commit
`a88be462efa38e479070b89e0a4c90b4b7b202da` through an authenticated checkout or
reviewed file transfer. Verify that its Git blob is
`4fa5e025c1516ebaaff260177b572f3253a61aa1`.

Do not execute a moving `main` branch as root.

## Direct unattended installation

Export a short-lived GitHub token in the current user shell:

```bash
export HOSTPANEL_GITHUB_TOKEN='github_pat_REPLACE_ME'
```

Set the domain and run `auto-install.sh` without prompts:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" HP_PANEL_DOMAIN=example.com HP_GITHUB_TOKEN_FD=3 bash -c 'exec 3<&0; exec bash auto-install.sh'
```

Then clear the user-shell variable:

```bash
unset HOSTPANEL_GITHUB_TOKEN
```

The domain `example.com` becomes `panel.example.com`. The current SSH source is
carried across `sudo` and converted to a single-address `/32` or `/128` for the
administrative firewall rule.

When the machine already has a valid FQDN from `hostname -f`, omit
`HP_PANEL_DOMAIN`:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" HP_GITHUB_TOKEN_FD=3 bash -c 'exec 3<&0; exec bash auto-install.sh'
```

To avoid SSH-source detection, provide an explicit administrative network:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env HP_PANEL_DOMAIN=example.com HP_PANEL_ADMIN_CIDR=192.0.2.10/32 HP_GITHUB_TOKEN_FD=3 bash -c 'exec 3<&0; exec bash auto-install.sh'
```

The token is supplied on inherited descriptor 3. It is not placed in a URL,
normal command argument, or root environment variable.

## Cloud-init, Terraform, and image builders

Provision the immutable `auto-install.sh` file and provide the GitHub token
through a root-only secret file. Then invoke the same engine directly:

```bash
sudo env \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_GITHUB_TOKEN_FILE=/run/secrets/hostpanel_github_token \
  bash auto-install.sh
```

Use [`examples/cloud-init-hostpanel.yaml`](examples/cloud-init-hostpanel.yaml) as
the cloud-init reference. It expects the provider or secret manager to create
`/run/secrets/hostpanel_github_token`; it deliberately does not embed a token in
instance metadata.

Terraform provisioners and image builders should use the same immutable script,
environment variables, and external-secret pattern.

## Provisioning secrets

`auto-install.sh` accepts:

```text
HP_GITHUB_TOKEN_FILE=/path/to/root-only-token
HP_GITHUB_TOKEN_FD=3
```

It also discovers:

```text
$CREDENTIALS_DIRECTORY/github-token
/run/secrets/hostpanel_github_token
/etc/hostpanel/install-github.token
```

Token files must be root-owned, single-linked regular files with mode `0400` or
`0600`. They are opened with no-follow semantics and rejected if their identity
changes while being read.

## Configuration

```text
HP_PANEL_HOST=panel.example.com
HP_PANEL_DOMAIN=example.com
HP_PANEL_ADMIN_CIDR=192.0.2.10/32
HP_MTA=postfix
HP_ROLES="control web database mail dns backup edge"
HP_REINSTALL=yes
HP_CHECK_ONLY=yes
HP_POST_INSTALL_CHECK=no
HP_GITHUB_TOKEN_FILE=/run/secrets/hostpanel_github_token
HP_GITHUB_TOKEN_FD=3
```

Omitting `HP_ROLES` installs all roles. Supported roles are `control`, `web`,
`database`, `mail`, `dns`, `backup`, and `edge`.

`HP_CHECK_ONLY=yes` runs the complete preflight without changing the server.
`HP_REINSTALL=yes` is required for an explicit replacement of an existing
installation.

## Automatic detection and fail-closed behavior

When `HP_PANEL_HOST` is omitted, the installer accepts a valid existing FQDN
from `hostname -f`. Alternatively, `HP_PANEL_DOMAIN=example.com` resolves to
`panel.example.com`.

When `HP_PANEL_ADMIN_CIDR` is omitted, the active SSH source is converted to a
single-address `/32` or `/128`. Cloud-init normally has no SSH source, so set the
CIDR explicitly there.

Missing or unsafe hostname and CIDR values fail closed. The panel is never made
public automatically.

## Installation behavior

`auto-install.sh` automatically:

1. installs missing bootstrap prerequisites;
2. validates the hostname, administrative CIDR, MTA, and selected roles;
3. verifies the immutable automatic launcher, bootstrap, and production validator;
4. runs the complete preflight before mutation;
5. installs all roles with Postfix by default;
6. verifies version `3.4.1`;
7. runs the production validator and `hostpanel-doctor`;
8. writes machine-readable status to `/var/lib/hostpanel/auto-install-status.json`.

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
