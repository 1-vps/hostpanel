# HostPanel installation

HostPanel provides two installation paths:

- `auto-install.sh` for cloud-init, Terraform, image builders, and unattended VPS provisioning;
- `quick-install.sh` for an interactive DirectAdmin-style installation.

Both paths install version `3.4.1` from reviewed, immutable Git objects. The
fully automatic launcher is pinned to commit:

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

## One-line fully automatic installation

Export the short-lived token in the current shell. The value is piped to `sudo`;
it is not included in the command line, URL, or root process environment.

```bash
export HOSTPANEL_GITHUB_TOKEN='github_pat_REPLACE_ME'
```

Set your domain in the following single command. It derives the panel hostname
as `panel.example.com`, carries the current SSH source across `sudo`, installs
missing `curl`/`git` prerequisites, downloads the immutable launcher, verifies
its Git blob, passes the token on inherited descriptor 3, and starts the complete
non-interactive installation:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" HP_PANEL_DOMAIN=example.com bash -c 'set -Eeuo pipefail; umask 077; T=$(cat); [[ "$T" =~ ^[A-Za-z0-9_]{20,512}$ ]] || { echo "Invalid GitHub token input" >&2; exit 1; }; if ! command -v curl >/dev/null || ! command -v git >/dev/null; then if command -v apt-get >/dev/null; then export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq ca-certificates curl git; elif command -v dnf >/dev/null; then dnf -y install ca-certificates curl git; else echo "Unsupported package manager" >&2; exit 1; fi; fi; D=$(mktemp -d /tmp/hostpanel-one-line.XXXXXX); trap "rm -rf -- \"$D\"" EXIT; { printf "header = \"Accept: application/vnd.github.raw+json\"\n"; printf "header = \"Authorization: Bearer %s\"\n" "$T"; printf "header = \"X-GitHub-Api-Version: 2022-11-28\"\n"; } >"$D/curl"; chmod 0600 "$D/curl"; curl --proto "=https" --tlsv1.2 -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 180 --config "$D/curl" "https://api.github.com/repos/1-vps/hostpanel/contents/auto-install.sh?ref=a88be462efa38e479070b89e0a4c90b4b7b202da" -o "$D/install"; rm -f "$D/curl"; [[ "$(git hash-object "$D/install")" == 4fa5e025c1516ebaaff260177b572f3253a61aa1 ]] || { echo "Automatic launcher verification failed" >&2; exit 1; }; chmod 0700 "$D/install"; exec 3<<<"$T"; T=; unset T; HP_GITHUB_TOKEN_FD=3 bash "$D/install"'
unset HOSTPANEL_GITHUB_TOKEN
```

When the machine already has a valid FQDN from `hostname -f`, remove
`HP_PANEL_DOMAIN=example.com`. To avoid SSH-source detection, add an explicit
administrative network before `bash -c`:

```text
HP_PANEL_ADMIN_CIDR=192.0.2.10/32
```

The command performs no prompts. It automatically:

1. installs missing bootstrap prerequisites;
2. validates or detects the panel hostname and administrative CIDR;
3. keeps the token out of URLs and normal process arguments;
4. verifies the immutable automatic launcher, interactive launcher, bootstrap, and production validator;
5. runs the complete preflight before mutation;
6. installs all roles with Postfix by default;
7. verifies version `3.4.1`, runs the production validator, and runs `hostpanel-doctor`;
8. writes machine-readable status to `/var/lib/hostpanel/auto-install-status.json`.

## Automatic detection

When `HP_PANEL_HOST` is omitted, the installer accepts a valid existing FQDN
from `hostname -f`. Alternatively set:

```text
HP_PANEL_DOMAIN=example.com
```

which resolves to `panel.example.com`.

When `HP_PANEL_ADMIN_CIDR` is omitted, the active SSH source is converted to a
single-address `/32` or `/128`. The one-line command explicitly carries
`SSH_CONNECTION` across `sudo`. Cloud-init normally has no SSH source, so set the
CIDR explicitly there. Missing or unsafe values fail closed; the panel is never
made public automatically.

## Provisioning secrets

For cloud-init, systemd credentials, Terraform provisioners, or secret-manager
integrations, the installer also accepts:

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
