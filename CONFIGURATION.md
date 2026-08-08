# HostPanel configuration

<!-- {{HOSTPANEL_RELEASE_VERSION}}=3.4.1 -->
<!-- {{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5 -->
<!-- {{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable -->
<!-- {{HOSTPANEL_PUBLICATION_ALLOWED}}=false -->

This reference describes supported installation controls for HostPanel release
**3.4.1**, derived from signed base **3.4.0-hardened-r5**. The authoritative
machine-readable state is [`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json).

Do not execute an unpinned branch script as root. Obtain the exact reviewed full
commit SHA and required Git blob identifiers from [`SETUP.md`](SETUP.md).

## Private-repository authentication

Use a short-lived token limited to **Contents: Read-only**. Fetch bootstrap and
validation material through the GitHub Contents API and verify documented Git
blob IDs before execution.

Provide transient Git authentication through process environment variables, not
through a URL, remote, shell history, or command-line argument:

```text
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
GIT_CONFIG_VALUE_0=AUTHORIZATION: basic <base64(x-access-token:TOKEN)>
GIT_TERMINAL_PROMPT=0
```

Treat `GIT_CONFIG_VALUE_0` as secret material. Unset all variables and remove any
temporary token files before repository-controlled helpers or the installer run.

## Required settings

### `HP_REPO_REF`

A reviewed full 40-character Git commit SHA. The bootstrap fetches this exact
commit and verifies executable overlay files against their Git objects.

Never substitute a branch name, shortened SHA, or mutable tag.

### `HP_PANEL_HOST`

The panel's fully qualified domain name, for example `panel.example.com`. It is
used for certificate subjects, routing, and production validation.

### `HP_PANEL_ADMIN_CIDR`

The IPv4 or IPv6 address or network allowed to reach the panel, for example:

```text
192.0.2.10/32
2001:db8:100::/64
```

When installation runs over SSH, the installer may derive the client address.
Without either a supplied administrative CIDR or a safe SSH source, installation
fails closed.

## Network settings

### `HP_PANEL_PORT`

Overrides the public panel port. The default is `2222`. Verify the port in both
the provider firewall and operating-system firewall, including after reboot.

### `HP_ALLOW_PUBLIC_PANEL`

Setting this to `yes` explicitly accepts public panel exposure. Leave it unset
for normal installations and prefer a narrow `HP_PANEL_ADMIN_CIDR`.

## Package and repository policy

### `HP_MULTI_PHP_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured by the operator. HostPanel must not execute mutable third-party
repository bootstrap scripts.

### `HP_RSPAMD_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured. Prefer distribution packages where supported.

## Mail settings

Use `--mta postfix` or `--mta exim`. The installer validates the selected MTA.
Required mail services and the final doctor check fail installation when they do
not start successfully.

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

Use the same role selection during preflight and mutating installation.

## Reinstall and recovery

Run `--reinstall --check` before a mutating reinstall. Every mutating run creates
a root-owned safety snapshot below:

```text
/var/backups/hostpanel-install/
```

Rollback is best effort, not fully transactional. Keep a provider snapshot and
console access for production changes.

## Validation settings

The expected installed application version is:

```text
HP_EXPECTED_VERSION=3.4.1
```

Common validator inputs include:

```text
HP_PANEL_HOST=panel.example.com
HP_EXPECTED_PUBLIC_IP=192.0.2.20
```

Run the validator before and after a verified reboot as documented in
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md).

## Managed configuration

Installed configuration is stored below `/opt/hostpanel` and `/etc/hostpanel`.
Do not loosen ownership or permissions on configuration, credentials, Redis ACL
material, installer snapshots, authentication files, or validation reports.

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
