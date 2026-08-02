# HostPanel setup

This guide installs the `3.4.0-hardened-r6` working release from the signed
`3.4.0-hardened-r5` base release through reviewed installer overlay commit:

```text
9c38d0095563ea33efd14124babfd29556c0da46
```

The signed application writes `3.4.0` to `/opt/hostpanel/VERSION`. The
`hardened-r5` and `hardened-r6` values are signed-package and installer-overlay
revision labels.

## Security model

The bootstrap contains the long-lived release verification key and uses it to
authenticate the signed base archive. It separately verifies every installer
overlay file against the operator-supplied full Git commit object. The generated
root installer is derived deterministically from a preserved base installer and
fails closed when an expected replacement or Git object does not match.

This repository is private. Anonymous `raw.githubusercontent.com` downloads are
not a supported installation path. Use a short-lived, repository-scoped GitHub
fine-grained token or GitHub App installation token with only **Contents:
Read-only** permission. Never put the token in a URL or command-line argument.

HostPanel changes packages, firewall rules, web and mail services, databases,
DNS, scheduled jobs, and customer data paths. Validate on a disposable server,
retain provider-console access, and create a provider snapshot before production
changes.

## Requirements

- Ubuntu 22.04, 24.04, or 26.04; Debian 12 or 13; Rocky Linux 9 or 10; or AlmaLinux 9 or 10
- x86-64/AMD64 or ARM64/AArch64
- at least 2 GB RAM and 10 GB free on `/`
- root access
- a valid panel hostname such as `panel.example.com`
- an administrative IP or CIDR
- a short-lived GitHub read-only token for this repository

Ubuntu 26.04 uses distribution-provided PHP 8.5 and Rspamd packages. Automatic
third-party repository setup is disabled. Preconfigure reviewed repositories
when required and keep `HP_MULTI_PHP_REPO=off` and `HP_RSPAMD_REPO=off` during
installation.

A full installation selects all roles unless `--role` is supplied: `control`,
`web`, `database`, `mail`, `dns`, `backup`, and `edge`.

## 1. Prepare DNS, access, and recovery

Create an `A` record for the panel hostname. Add an `AAAA` record only when IPv6
is configured and protected by the same firewall policy.

```bash
getent ahosts panel.example.com
```

Identify the source address or network that must retain panel access, for
example:

```text
192.0.2.10/32
2001:db8:100::/64
```

The installer detects the active SSH port, opens required management access
before enabling a default-deny firewall, and schedules a timed rollback until
installation completes. Keep provider-console access and a provider snapshot.

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

## 3. Create the authenticated installer driver

Review the block before running it. Set `PANEL_HOST`, `ADMIN_CIDR`, `MTA`, and
`REINSTALL` at the top. The script prompts without echo for the short-lived
read-only token, downloads two files from the exact reviewed commit through the
GitHub Contents API, verifies their Git blob IDs, installs HostPanel, and removes
all authentication state on success or failure.

```bash
sudo tee /root/install-hostpanel-private.sh >/dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REVIEWED_COMMIT_SHA=9c38d0095563ea33efd14124babfd29556c0da46
BOOTSTRAP_BLOB=5d5bd1af8742703396e911a34163cd2992581737
VALIDATOR_BLOB=2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9
REPOSITORY_API=https://api.github.com/repos/1-vps/hostpanel

PANEL_HOST=panel.example.com
ADMIN_CIDR=192.0.2.10/32
MTA=postfix
REINSTALL=no

[[ "$EUID" -eq 0 ]] || {
  echo 'Run this script as root.' >&2
  exit 1
}
case "$MTA" in
  postfix|exim) ;;
  *) echo 'MTA must be postfix or exim.' >&2; exit 1 ;;
esac
case "$REINSTALL" in
  yes|no) ;;
  *) echo 'REINSTALL must be yes or no.' >&2; exit 1 ;;
esac

AUTH_FILE=""
cleanup_auth(){
  local status=$?
  [[ -z "${AUTH_FILE:-}" ]] || rm -f -- "$AUTH_FILE"
  unset GH_READ_TOKEN GIT_AUTH_HEADER
  unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT
  return "$status"
}
trap cleanup_auth EXIT

read -r -s -p 'GitHub Contents:Read token: ' GH_READ_TOKEN
printf '\n'
[[ -n "$GH_READ_TOKEN" \
   && "$GH_READ_TOKEN" != *$'\n'* \
   && "$GH_READ_TOKEN" != *$'\r'* \
   && "$GH_READ_TOKEN" != *'"'* ]] || {
  echo 'Invalid token input.' >&2
  exit 1
}

AUTH_FILE="$(mktemp /root/hostpanel-github-auth.XXXXXX)"
{
  printf 'header = "Accept: application/vnd.github.raw+json"\n'
  printf 'header = "Authorization: Bearer %s"\n' "$GH_READ_TOKEN"
  printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
} > "$AUTH_FILE"
chmod 600 "$AUTH_FILE"

curl --fail --location --silent --show-error \
  --config "$AUTH_FILE" \
  "${REPOSITORY_API}/contents/bootstrap-install.sh?ref=${REVIEWED_COMMIT_SHA}" \
  -o /root/bootstrap-install.sh
curl --fail --location --silent --show-error \
  --config "$AUTH_FILE" \
  "${REPOSITORY_API}/contents/tools/validate-production-vm.sh?ref=${REVIEWED_COMMIT_SHA}" \
  -o /root/validate-production-vm.sh

rm -f -- "$AUTH_FILE"
AUTH_FILE=""
chmod 700 /root/bootstrap-install.sh /root/validate-production-vm.sh

[[ "$(git hash-object /root/bootstrap-install.sh)" == "$BOOTSTRAP_BLOB" ]] || {
  echo 'Bootstrap Git blob mismatch.' >&2
  exit 1
}
[[ "$(git hash-object /root/validate-production-vm.sh)" == "$VALIDATOR_BLOB" ]] || {
  echo 'Validator Git blob mismatch.' >&2
  exit 1
}
bash -n /root/bootstrap-install.sh
bash -n /root/validate-production-vm.sh

GIT_AUTH_HEADER="$(printf 'x-access-token:%s' "$GH_READ_TOKEN" | base64 | tr -d '\r\n')"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0='http.https://github.com/.extraheader'
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $GIT_AUTH_HEADER"
export GIT_TERMINAL_PROMPT=0
unset GH_READ_TOKEN GIT_AUTH_HEADER

install_args=(--mta "$MTA")
if [[ "$REINSTALL" == yes ]]; then
  install_args=(--reinstall "${install_args[@]}")
fi
common_env=(
  HP_REPO_REF="$REVIEWED_COMMIT_SHA"
  HP_PANEL_HOST="$PANEL_HOST"
  HP_PANEL_ADMIN_CIDR="$ADMIN_CIDR"
  HP_MULTI_PHP_REPO=off
  HP_RSPAMD_REPO=off
)

env "${common_env[@]}" \
  bash /root/bootstrap-install.sh --check "${install_args[@]}"
env "${common_env[@]}" \
  bash /root/bootstrap-install.sh "${install_args[@]}"

cleanup_auth
trap - EXIT
printf 'HostPanel installation completed from reviewed commit %s.\n' \
  "$REVIEWED_COMMIT_SHA"
SCRIPT
sudo chmod 700 /root/install-hostpanel-private.sh
sudo /root/install-hostpanel-private.sh
```

Delete the driver after reviewing the installation result. It contains no token,
but removing one-off root scripts reduces future ambiguity.

```bash
sudo rm -f /root/install-hostpanel-private.sh
```

The preflight validates inputs, host capacity, ports, roles, and MTA without
installing packages or changing services. The mutating command runs only after
the same preflight succeeds.

## 4. Reinstall or interrupted-run recovery

Set this line in the reviewed driver before running it:

```bash
REINSTALL=yes
```

Every mutating run creates a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

The directory is mode `0700`; archives and absence manifests are mode `0600`.
It is separate from panel-managed customer backups. Rollback is best-effort
because package scripts and external service effects cannot be fully
transactional.

## 5. Verify after installation

```bash
cat /opt/hostpanel/VERSION
readlink -f /opt/hostpanel/venv
sudo nginx -t
sudo systemctl status hostpanel nginx --no-pager --full
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor
```

Expected installed application version:

```text
3.4.0
```

Inspect the root-only installer log:

```text
/var/log/hostpanel-install.log
```

The validator was downloaded and blob-verified before installation. Run its
non-destructive checks:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

Then follow [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for reboot and
external acceptance.

## 6. Least-privilege QEMU acceptance

The QEMU workflow boots a checksum-pinned Ubuntu 24.04 image, provisions an
ephemeral SSH key, installs the reviewed commit, validates services, performs a
real reboot, and collects bounded non-sensitive evidence.

For the private repository, GitHub's per-run read-only token is exposed only to
the installation step. The plain and encoded runner variables are removed before
QEMU starts; transient guest Git authentication is deleted after fetch on
success and failure. No repository-defined secret is required.

Run it from the Actions UI or with GitHub CLI:

```bash
gh workflow run qemu-vm-acceptance.yml -f mta=postfix
gh run watch
```

Use `mta=exim` for the Exim path. The full VM job is skipped for pull requests
from forks.

## 7. Provider-backed acceptance

The manual `vps-acceptance` workflow is environment-gated and destructive. It:

- requires the exact confirmation phrase and a provider-snapshot secret;
- checks out the hard-pinned reviewed commit rather than the dispatch ref;
- verifies checkout `HEAD` before any VPS connection and before installation;
- uses strict SSH host verification;
- copies the blob-reviewed bootstrap and validator from that checkout;
- removes transient runner and remote Git authentication on all exit paths;
- reboots the VM and gathers bounded acceptance evidence.

Use it only with a disposable VM and protected `vps-acceptance` environment.

## 8. Production acceptance

Before serving customers:

1. install on a disposable systemd VM of the exact target OS;
2. run the validator before and after a verified reboot;
3. test panel login, web, database, DNS, mail, backup, and restore paths;
4. verify quota enforcement on the actual customer-data filesystem;
5. replace self-signed certificates with trusted certificates;
6. configure reverse DNS, SPF, DKIM, and DMARC;
7. retain and test a provider-level recovery path.

Current deployable overlay release: **3.4.1** (signed base source: `3.4.0`).
