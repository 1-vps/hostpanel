# HostPanel configuration

<!-- {{HOSTPANEL_RELEASE_VERSION}}=3.4.1 -->
<!-- {{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5 -->
<!-- {{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable -->
<!-- {{HOSTPANEL_PUBLICATION_ALLOWED}}=false -->

This reference describes the reviewed installation controls for HostPanel `3.4.1`,
derived from signed base **3.4.0-hardened-r5**. The authoritative machine-readable
release state is [`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json).

The only documented installation entry point is the immutable `auto-install.sh`
engine described in [`SETUP.md`](SETUP.md). Do not execute a moving branch script
as root.

**Reviewed automatic-installer commit:**

```text
1a86d380e7ebab287c767d183013b599cb116f7f
```

**Verified automatic-installer Git blob:**

```text
db23963e101b9194994da2ff8077b40a6b1cb99c
```

**Reviewed product commit fetched by the installer chain:**

```text
755dcd5e47b7c82404b267e8df4dec27626fe341
```

**Installed `/opt/hostpanel/VERSION`:**

```text
3.4.1
```

## Private repository authentication

This repository is private. Follow [`SETUP.md`](SETUP.md) to obtain the exact
reviewed `auto-install.sh` bytes through an authenticated checkout or reviewed
file transfer and verify its Git blob before execution.

The installer accepts a short-lived GitHub token with **Contents: Read-only**
access to `1-vps/hostpanel`. Interactive/unattended launchers pass the token on
an inherited descriptor; automation may instead use a root-owned, single-linked
mode `0400` or `0600` file through `HP_GITHUB_TOKEN_FILE`. Tokens must never be
placed in a URL, normal command argument, shell profile, repository remote,
logs, or evidence.

The automatic installer and its delegated launchers contain their reviewed Git
commits and blob IDs. Operators do not select a moving `HP_REPO_REF` for the
normal installation path.

## Required installation settings

### `HP_PANEL_HOST`

The panel's fully qualified domain name, for example `panel.example.com`. The
installer uses it for certificate subjects, routing, and validation.

### `HP_PANEL_DOMAIN`

An optional base domain such as `example.com`. The automatic installer converts
it to `panel.example.com`. An explicitly supplied invalid domain fails closed and
does not fall back to the machine hostname.

### `HP_PANEL_ADMIN_CIDR`

The IPv4 or IPv6 address or network allowed to reach the panel, for example:

```text
192.0.2.10/32
2001:db8:100::/64
```

When installation is launched over SSH, the installer can derive the client
address. Without either a supplied administrative CIDR or an SSH source,
installation fails closed.

## Network settings

### `HP_PANEL_PORT`

Overrides the public panel port. The default is `2222`. Verify the selected port
in both provider and operating-system firewalls and after reboot.

### `HP_ALLOW_PUBLIC_PANEL`

Setting this to `yes` explicitly accepts public panel exposure. Leave it unset
for normal installations and prefer a narrow `HP_PANEL_ADMIN_CIDR`.

## Package and repository policy

### `HP_MULTI_PHP_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured by the operator. The installer does not execute mutable third-party
repository bootstrap scripts.

### `HP_RSPAMD_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured. Ubuntu 26.04 uses its distribution-provided Rspamd package.

## Mail settings

Use `HP_MTA=postfix` or `HP_MTA=exim`. The selected MTA is validated before
installation. Required mail services and the final doctor check fail the
installation when they do not start successfully.

## Role selection

A full installation selects all roles. Set `HP_ROLES` to a space-separated
subset of reviewed roles:

```text
control web database mail dns backup edge
```

Use the same role selection during preflight and the mutating installation.

## Check-only, reinstall, and validation controls

- `HP_CHECK_ONLY=yes` runs preflight without persistent installation mutation.
- `HP_REINSTALL=yes` is required for an explicit replacement of an existing
  installation.
- `HP_POST_INSTALL_CHECK=no` skips the production validator and doctor; the
  machine-readable installation result is marked `unverified`, not healthy.

Every mutating installer run creates a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

Rollback is best-effort rather than fully transactional. Keep a provider-level
snapshot and console access for production changes.

## Validation settings

The production validator expects the installed application version:

```text
HP_EXPECTED_VERSION=3.4.1
```

Other common validator inputs:

```text
HP_PANEL_HOST=panel.example.com
HP_EXPECTED_PUBLIC_IP=192.0.2.20
```

Run the validator before and after a verified reboot as documented in
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md).

## Managed configuration

The installed panel configuration is stored under `/opt/hostpanel` and
`/etc/hostpanel`. Do not loosen ownership or permissions on configuration,
credentials, Redis ACL material, installer snapshots, authentication files, or
validation reports.

After supported configuration changes, rerun:

```bash
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor --quiet
sudo env \
  HP_EXPECTED_VERSION=3.4.1 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

## Release boundary

Release `3.4.1` is deployable but production publication is blocked. Do not infer
approval from a successful local installation. Publication requires every blocker
in `RELEASE-MANIFEST.json` to be removed through reviewed evidence and the
manifest status to become `production-ready` with
`production_publish_allowed=true`.
