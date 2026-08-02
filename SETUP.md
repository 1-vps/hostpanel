# HostPanel setup

HostPanel installs deployable release `3.4.1` from reviewed product commit:

```text
d50ccea35aa6356f7f815a606fa91f6186b66a6f
```

The one-line launcher itself is pinned to immutable commit:

```text
c534a220ad775b4fe94e53ae297d1698444c1388
```

The repository is private, so the launcher asks once for a short-lived GitHub
fine-grained token with **Contents: Read-only** access to `1-vps/hostpanel`.
The token is written only to a root-owned temporary directory, is never placed
in a URL or normal command argument, and is removed when the launcher exits.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root or passwordless sudo access
- a valid panel hostname, for example `panel.example.com`
- an administrative IP or CIDR
- a short-lived GitHub token with Contents: Read-only access

Keep provider-console access and create a provider snapshot before installing.
HostPanel changes packages, firewall rules, web/mail/DNS services, databases,
scheduled jobs, and customer data paths.

## One-line installation

Copy and run this exact line:

```bash
sudo bash -c 'set -euo pipefail; umask 077; D=$(mktemp -d /tmp/hostpanel-link.XXXXXX); trap "rm -rf -- \"$D\"" EXIT; read -rsp "GitHub Contents:Read token: " T; echo; printf "%s" "$T" >"$D/token"; { printf "header = \"Accept: application/vnd.github.raw+json\"\\n"; printf "header = \"Authorization: Bearer %s\"\\n" "$T"; printf "header = \"X-GitHub-Api-Version: 2022-11-28\"\\n"; } >"$D/curl"; unset T; curl --proto "=https" --tlsv1.2 -fsSL --config "$D/curl" "https://api.github.com/repos/1-vps/hostpanel/contents/quick-install.sh?ref=c534a220ad775b4fe94e53ae297d1698444c1388" -o "$D/install"; chmod 700 "$D/install"; HP_GITHUB_TOKEN_FILE="$D/token" bash "$D/install"'
```

The launcher then asks for:

1. the GitHub token, without echo;
2. the panel hostname;
3. the administrative CIDR (the active SSH source is suggested when available);
4. final confirmation.

It installs missing bootstrap prerequisites, verifies the immutable launcher,
downloads the reviewed bootstrap and production validator from the exact product
commit, verifies both Git blob IDs, runs `--check`, and only then performs the
mutating installation.

The default MTA is Postfix and all roles are installed.

## Common options

To use Exim, selected roles, or non-interactive values, append options to the
final `bash "$D/install"` portion of the one-line command:

```text
--hostname panel.example.com
--admin-cidr 192.0.2.10/32
--mta exim
--role web
--role database
--reinstall
--check-only
--yes
```

For example, the final portion can be changed to:

```bash
HP_GITHUB_TOKEN_FILE="$D/token" bash "$D/install" \
  --hostname panel.example.com \
  --admin-cidr 192.0.2.10/32 \
  --mta postfix \
  --yes
```

`--check-only` performs the complete preflight without installing or changing
services.

## DirectAdmin-style public URL

A truly short anonymous command such as:

```bash
curl -fsSL https://installer.example/install.sh | sudo bash
```

requires a public HTTPS endpoint that serves only the reviewed
`quick-install.sh`. The product repository remains private, so GitHub cannot
currently provide an anonymous raw link. The committed launcher is ready to be
served unchanged from such an endpoint; it will still prompt for the private
repository token and verify the pinned product commit.

Do not serve `main` or another moving branch directly as a root installer.
Publish the exact launcher bytes from commit `c534a220ad775b4fe94e53ae297d1698444c1388`.

## Reinstall and rollback

Use `--reinstall` only after a successful `--check-only` run. Every mutating
installation creates a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

Rollback is best-effort because operating-system package scripts and external
service side effects are not fully transactional.

## Verify

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
nginx -t
systemctl status hostpanel nginx --no-pager --full
/opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor
bash /root/validate-production-vm.sh --check
```

Expected version:

```text
3.4.1
```

Installer log:

```text
/var/log/hostpanel-install.log
```
