# HostPanel configuration

This reference describes the reviewed installation controls for
`3.4.0-hardened-r6`. Supply settings to the pinned bootstrap. Do not execute an
unpinned branch script as root.

**Reviewed installer and VM-harness commit:**

```text
9c38d0095563ea33efd14124babfd29556c0da46
```

**Installed `/opt/hostpanel/VERSION`:**

```text
3.4.0
```

## Private repository authentication

This repository is private. Follow [`SETUP.md`](SETUP.md) to fetch the bootstrap
and validator through the GitHub Contents API with a short-lived token limited to
**Contents: Read-only**, then verify their Git blob IDs before execution.

The bootstrap fetches the reviewed overlay through Git. Provide transient Git
authentication with process environment variables rather than a credential in a
URL, remote, or command-line argument:

```text
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
GIT_CONFIG_VALUE_0=AUTHORIZATION: basic <base64(x-access-token:TOKEN)>
GIT_TERMINAL_PROMPT=0
```

Treat `GIT_CONFIG_VALUE_0` as a secret even though it is encoded. Unset all four
variables immediately after preflight and installation, remove temporary token
files, and never persist them in shell profiles, systemd units, logs, evidence,
or repository configuration.

## Required installation settings

### `HP_REPO_REF`

The reviewed full 40-character Git commit SHA. The bootstrap fetches this exact
commit and verifies every executable overlay against its Git object before use.
For the current working release:

```text
HP_REPO_REF=9c38d0095563ea33efd14124babfd29556c0da46
```

### `HP_PANEL_HOST`

The panel's fully qualified domain name, for example `panel.example.com`. The
installer uses it for certificate subjects, routing, and validation.

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

Use `--mta postfix` or `--mta exim`. The selected MTA is validated before
installation. Required mail services and the final doctor check fail the
installation when they do not start successfully.

## Role selection

A full installation selects all roles. Use repeated `--role` arguments to limit
a node to reviewed roles:

```text
control
web
database
mail
dns
backup
edge
```

Use the same role selection during preflight and the mutating installation.

## Reinstall and recovery

Use `--reinstall --check` before a mutating reinstall. Every mutating run creates
a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

Rollback is best-effort rather than fully transactional. Keep a provider-level
snapshot and console access for production changes.

## Validation settings

The production validator expects the installed application version, not the
working-release label:

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

Current deployable overlay release: **3.4.1** (signed base source: `3.4.0`).
