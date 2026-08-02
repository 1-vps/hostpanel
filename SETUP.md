# HostPanel installation

HostPanel provides two installation paths:

- `auto-install.sh` for cloud-init, Terraform, image builders, and unattended VPS provisioning;
- `quick-install.sh` for an interactive DirectAdmin-style installation.

Both paths install version `3.4.1` from reviewed, immutable Git objects. The
fully automatic launcher is pinned to commit:

```text
a88be462efa38e479070b89e0a4c90b4b7b202da
```

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root access
- a valid panel FQDN
- an administrative IP or CIDR
- a short-lived GitHub token with **Contents: Read-only** access to `1-vps/hostpanel`

Create a provider snapshot and retain console access before installation.

## Fully automatic installation

Create a root-only token file without a trailing newline:

```bash
sudo install -d -o root -g root -m 0700 /etc/hostpanel
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" \
  | sudo tee /etc/hostpanel/install-github.token >/dev/null
sudo chown root:root /etc/hostpanel/install-github.token
sudo chmod 0600 /etc/hostpanel/install-github.token
unset HOSTPANEL_GITHUB_TOKEN
```

Replace the hostname and CIDR, then run this one line:

```bash
sudo env HP_PANEL_HOST=panel.example.com HP_PANEL_ADMIN_CIDR=192.0.2.10/32 HP_GITHUB_TOKEN_FILE=/etc/hostpanel/install-github.token bash -c 'set -Eeuo pipefail; umask 077; command -v curl >/dev/null || { if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq ca-certificates curl; elif command -v dnf >/dev/null; then dnf -y install ca-certificates curl; else exit 1; fi; }; D=$(mktemp -d /tmp/hostpanel-auto-link.XXXXXX); trap "rm -rf -- \"$D\"" EXIT; T=$(cat "$HP_GITHUB_TOKEN_FILE"); [[ "$T" =~ ^[A-Za-z0-9_]{20,512}$ ]]; { printf "header = \"Accept: application/vnd.github.raw+json\"\n"; printf "header = \"Authorization: Bearer %s\"\n" "$T"; printf "header = \"X-GitHub-Api-Version: 2022-11-28\"\n"; } >"$D/curl"; unset T; curl --proto "=https" --tlsv1.2 -fsSL --retry 5 --config "$D/curl" "https://api.github.com/repos/1-vps/hostpanel/contents/auto-install.sh?ref=a88be462efa38e479070b89e0a4c90b4b7b202da" -o "$D/install"; chmod 0700 "$D/install"; bash "$D/install"'
```

The command performs no prompts. It automatically:

1. installs missing bootstrap prerequisites;
2. validates or detects the panel hostname and administrative CIDR;
3. reads the token from a root-only file or inherited descriptor;
4. verifies the immutable automatic launcher, interactive launcher, bootstrap, and production validator;
5. runs the complete preflight before mutation;
6. installs all roles with Postfix by default;
7. verifies version `3.4.1`, runs the production validator, and runs `hostpanel-doctor`;
8. writes machine-readable status to `/var/lib/hostpanel/auto-install-status.json`.

The token is never placed in a URL or normal command argument.

## Automatic detection

When `HP_PANEL_HOST` is omitted, the installer accepts a valid existing FQDN
from `hostname -f`. Alternatively set:

```text
HP_PANEL_DOMAIN=example.com
```

which resolves to `panel.example.com`.

When `HP_PANEL_ADMIN_CIDR` is omitted, the active SSH source is converted to a
single-address `/32` or `/128`. Cloud-init normally has no SSH source, so set the
CIDR explicitly there. Missing or unsafe values fail closed; the panel is never
made public automatically.

## Provisioning secrets

The installer accepts:

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

For a guided installation, use the immutable `quick-install.sh` command already
documented in the repository. It prompts for the token, hostname, CIDR, and
confirmation while retaining the same preflight and Git-object verification.

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
