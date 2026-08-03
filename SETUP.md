# HostPanel installation

`auto-install.sh` is the only documented HostPanel installation entry point. It
is the full cloud-init, Terraform, image-builder, and unattended VPS engine.

The base installation always uses **`nginx_apache`**: nginx is the public
HTTP/TLS frontend and Apache is the private backend on `127.0.0.1:8080`.
OpenLiteSpeed is not installed and no LiteSpeed repository is enabled during the
base installation. After installation, the verified `hostpanel-build` tool can
select `nginx_apache`, `nginx`, `apache`, or `openlitespeed`.

It installs HostPanel version `3.4.1` from reviewed, immutable Git objects. The
installer is pinned to commit:

```text
bac349ca093e4dd5c760efa03c8ec9410d33deef
```

Its verified Git blob is:

```text
0613e8b88af414b961c03d5adeee141e206ded2b
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
`bac349ca093e4dd5c760efa03c8ec9410d33deef` through an authenticated checkout or
reviewed file transfer. Verify that its Git blob is
`0613e8b88af414b961c03d5adeee141e206ded2b`.

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

The domain `example.com` becomes `panel.example.com`. Explicit invalid domain
values are rejected and never fall back to the machine hostname. The current SSH
source is carried across `sudo` and converted to a single-address `/32` or
`/128` for the administrative firewall rule.

When the machine already has a valid FQDN from `hostname -f`, omit
`HP_PANEL_DOMAIN`:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" HP_GITHUB_TOKEN_FD=3 bash -c 'exec 3<&0; exec bash auto-install.sh'
```

To avoid SSH-source detection, provide an explicit administrative network:

```bash
printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env HP_PANEL_DOMAIN=example.com HP_PANEL_ADMIN_CIDR=192.0.2.10/32 HP_GITHUB_TOKEN_FD=3 bash -c 'exec 3<&0; exec bash auto-install.sh'
```

The token is supplied on inherited descriptor 3. The descriptor is consumed and
removed from the child environment before delegation. The token is not placed in
a URL, normal command argument, or root environment variable.

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
instance metadata. The example downloads the immutable commit and verifies its
Git blob before execution.

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

A healthy repeated run does not require a token when the pinned local production
validator is already present. Credentials are loaded only when an immutable file
must be downloaded.

## Installation configuration

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

`HP_CHECK_ONLY=yes` runs preflight without installing missing operating-system
packages, copying persistent installer files into `/root`, installing
CustomBuild, or writing the HostPanel status file. Required commands must already
be installed. Temporary files and runtime locks are removed when the command
exits.

`HP_REINSTALL=yes` is required for an explicit replacement of an existing
installation.

`HP_POST_INSTALL_CHECK=no` skips the production validator and
`hostpanel-doctor`; the machine-readable result is marked `unverified`, not
healthy.

## CustomBuild webserver selection

The base install writes:

```text
webserver=nginx_apache
```

Review the active options and a proposed web-stack rebuild:

```bash
sudo hostpanel-build options
sudo hostpanel-build plan web
```

Choose one mode, then apply it explicitly:

```bash
sudo hostpanel-build set webserver nginx_apache
sudo hostpanel-build set webserver nginx
sudo hostpanel-build set webserver apache
sudo hostpanel-build set webserver openlitespeed
sudo hostpanel-build build web --apply
```

Changing the option alone does not modify packages, services, or domains.
`build web --apply` refreshes package metadata, performs package-candidate
preflight before service mutation, snapshots relevant configuration, validates
services, converts all managed domains through HostPanel's existing webserver
engine, disables unused backend services, and runs `hostpanel-doctor`.

nginx remains the public HTTP/TLS edge in every mode. In `apache` mode, nginx
proxies every customer request to Apache on `127.0.0.1:8080`; Apache therefore
handles all customer content and `.htaccess` without competing for ports 80 or
443. In `nginx_apache` mode, nginx additionally serves static files directly and
uses Apache for dynamic or fallback requests.

For `openlitespeed`, both `openlitespeed` and the complete matching LSPHP package
set for every selected PHP branch must be available. If any package is missing,
the operation stops before services are changed. During installation,
`lsws.service` is runtime-masked. HostPanel then forces WebAdmin to
`127.0.0.1:7080`, creates the private backend listener on `127.0.0.1:8088`,
validates the LSPHP binaries, and converts domains transactionally. A failed
multi-domain conversion is rolled back in reverse order. Mutable upstream build
scripts are never executed as root.

## Free SSL after installation

The verified CustomBuild command supports free ACME certificates from either
Let's Encrypt or ZeroSSL. Plan mode does not change nginx or request a
certificate:

```bash
sudo hostpanel-build ssl issue example.com --email admin@example.com --www
```

Issue with Let's Encrypt:

```bash
sudo hostpanel-build ssl issue example.com \
  --email admin@example.com \
  --provider letsencrypt \
  --www \
  --apply
```

ZeroSSL requires reusable EAB credentials. Save the EAB KID and HMAC in the
root-only files documented in `CUSTOMBUILD.md`, then run:

```bash
sudo hostpanel-build ssl issue example.com \
  --email admin@example.com \
  --provider zerossl \
  --www \
  --apply
```

Inspect or renew certificates:

```bash
sudo hostpanel-build ssl status example.com
sudo hostpanel-build ssl renew example.com --apply
```

nginx remains the public TLS edge in all four webserver modes. Existing
certificate directives are preserved when a secured domain is switched to
Apache content handling.

See [`CUSTOMBUILD.md`](CUSTOMBUILD.md) for component rebuilds, version reporting,
free SSL provider setup, and signed panel updates.

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

1. acquires a root-only bootstrap lock before package operations;
2. installs missing bootstrap prerequisites outside check-only mode;
3. validates the hostname, administrative CIDR, MTA, and selected roles;
4. verifies the immutable automatic launcher, bootstrap, validator, and CustomBuild inputs;
5. ignores user curl configuration for authenticated downloads;
6. runs the complete preflight before installation;
7. installs nginx as the public frontend and Apache as the private backend for the web role;
8. installs the verified `hostpanel-build` maintenance and free ACME SSL command after HostPanel succeeds;
9. installs all selected roles with Postfix by default;
10. verifies version `3.4.1`;
11. requires the production validator and `hostpanel-doctor` unless explicitly skipped;
12. writes machine-readable status to `/var/lib/hostpanel/auto-install-status.json`.

## Retry after an interrupted package installation

If an earlier run stopped during an APT transaction, repair Debian package state
before rerunning the fixed immutable installer:

```bash
sudo dpkg --configure -a
sudo apt-get -f install
```

Then rerun the normal `auto-install.sh` command. The new base path no longer
attempts to install `openlitespeed`; it completes with `nginx_apache` and leaves
OpenLiteSpeed as an explicit post-install CustomBuild choice.

## Idempotence and status

A second run against a healthy installed `3.4.1` verifies the validator and
doctor and exits successfully instead of reinstalling. A different installed
version fails unless `HP_REINSTALL=yes` is explicit.

Concurrent runs are rejected before package mutation by a root-only bootstrap
lock and during installation by the runtime lock under:

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
sudo cat /etc/hostpanel/webserver-mode
sudo hostpanel-build options
sudo hostpanel-build validate web
sudo hostpanel-build ssl status
sudo cat /var/lib/hostpanel/auto-install-status.json
sudo /opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor --quiet
sudo bash /root/validate-production-vm.sh --check
sudo systemctl status hostpanel nginx apache2 hostpanel-update.timer --no-pager --full
```

On RHEL-family systems, use `httpd` instead of `apache2` in the final status
command.

Installer log:

```text
/var/log/hostpanel-install.log
```
