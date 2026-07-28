# HostPanel configuration

This reference describes the reviewed installation controls for
`3.4.0-hardened-r6`. Supply installation settings as environment variables to
the pinned bootstrap. Do not execute an unpinned branch script as root.

Validated installer and VM-harness commit:

```text
01f171b489bc9971eab4e3ebe7aad58f10255124
```

## Required installation settings

### `HP_REPO_REF`

The reviewed full 40-character Git commit SHA. The bootstrap fetches this exact
commit and verifies every executable overlay against its Git object before use.

### `HP_PANEL_HOST`

The panel's fully qualified domain name, for example `panel.example.com`. The
installer uses this hostname for the panel certificate subject and DNS name.

### `HP_PANEL_ADMIN_CIDR`

The IPv4 or IPv6 address/network allowed to reach the panel. Examples:

```text
192.0.2.10/32
2001:db8:100::/64
```

When installation is launched over SSH, the installer can derive the client
address. Without either a supplied administrative CIDR or an SSH source,
installation fails closed.

## Network settings

### `HP_PANEL_PORT`

Overrides the public panel port. The default is `2222`. Verify that the selected
port is allowed by the provider firewall and remains reachable after reboot.

### `HP_ALLOW_PUBLIC_PANEL`

Setting this to `yes` explicitly accepts public panel exposure. Leave it unset
for normal installations. Prefer a narrow `HP_PANEL_ADMIN_CIDR` instead.

## Package and repository policy

### `HP_MULTI_PHP_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured by the operator. The installer does not execute mutable third-party
repository bootstrap scripts.

### `HP_RSPAMD_REPO`

Keep this set to `off` unless a reviewed external repository has already been
configured. Ubuntu 26.04 uses its distribution-provided Rspamd package.

## Mail settings

Use `--mta postfix` or `--mta exim` when invoking the installer. The selected MTA
is validated before installation. Required mail services and the final doctor
check fail the installation when they do not start successfully.

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

Run the same role selection during preflight and the mutating installation.

## Reinstall and recovery

Use `--reinstall --check` before a mutating reinstall. Every mutating run creates
a root-owned safety snapshot under:

```text
/var/backups/hostpanel-install/
```

Rollback is best-effort rather than fully transactional. Keep a provider-level
snapshot and console access for production changes.

## Managed configuration

The installed panel configuration is stored under `/opt/hostpanel` and
`/etc/hostpanel`. Do not loosen ownership or permissions on configuration,
credentials, Redis ACL material, installer snapshots, or validation reports.

After changing supported settings, rerun:

```bash
sudo /opt/hostpanel/venv/bin/python \
  /opt/hostpanel/app/hostpanel-doctor --quiet
sudo bash /root/validate-production-vm.sh --check
```

See [`SETUP.md`](SETUP.md) for installation and
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for systemd-VM acceptance.
