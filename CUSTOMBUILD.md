# HostPanel Build

`hostpanel-build` is HostPanel's CustomBuild-style maintenance CLI. It borrows the useful operational ideas from DirectAdmin CustomBuild—one options file, version visibility, component-oriented rebuilds, and separate panel/system updates—while retaining HostPanel's signed release updater and operating-system packages.

It does **not** compile unreviewed third-party source archives. Mutating commands require root, acquire a lock, write a private log, snapshot relevant configuration, validate the rebuilt service, restart it, and finish with `hostpanel-doctor`.

## Install the command

From a reviewed HostPanel source tree:

```bash
sudo bash tools/install-hostpanel-build.sh
```

This installs:

```text
/usr/local/sbin/hostpanel-build
/opt/hostpanel/tools/hostpanel-build
/etc/hostpanel/build.conf
```

The options file is root-owned and mode `0600`.

## Common commands

```bash
sudo hostpanel-build options
sudo hostpanel-build versions
sudo hostpanel-build versions --json
sudo hostpanel-build plan all
sudo hostpanel-build plan web
sudo hostpanel-build validate all
```

Planning and version commands do not change packages or services.

Change one option:

```bash
sudo hostpanel-build set webservers nginx,apache,openlitespeed
sudo hostpanel-build set php_versions 8.5,8.4,8.3,8.2
sudo hostpanel-build set database both
sudo hostpanel-build set mta postfix
```

Supported options:

```text
webservers=nginx,apache,openlitespeed
database=mariadb|postgresql|both
mta=postfix|exim
php_versions=7.4,8.0,8.1,8.2,8.3,8.4,8.5
```

The `all` target reads `/etc/hostpanel/roles.conf`; it only includes components belonging to roles installed on that node.

## Rebuild components

A build without `--apply` only prints the plan:

```bash
sudo hostpanel-build build nginx
sudo hostpanel-build build web
sudo hostpanel-build build all
```

Apply a reviewed plan explicitly:

```bash
sudo hostpanel-build build nginx --apply
sudo hostpanel-build build openlitespeed --apply
sudo hostpanel-build build php --apply
sudo hostpanel-build build database --apply
sudo hostpanel-build build mail --apply
sudo hostpanel-build build dns --apply
sudo hostpanel-build build all --apply
```

Available targets are `nginx`, `apache`, `openlitespeed`, `php`, `database`, `mail`, `dns`, `redis`, `web`, and `all`.

Before each component reinstall, relevant configuration is archived under:

```text
/var/backups/hostpanel/custombuild/
```

Execution details are written to:

```text
/var/log/hostpanel-build.log
```

## Check or apply updates

Check operating-system package updates:

```bash
sudo hostpanel-build update_versions
```

Apply them:

```bash
sudo hostpanel-build update_versions --apply
```

Check the next signed HostPanel release:

```bash
sudo hostpanel-build update_panel
```

Apply it through the existing signed updater:

```bash
sudo hostpanel-build update_panel --apply
```

`update_panel` never bypasses HostPanel's signed manifest, archive checksum, signature, and keyring verification.

## Safety model

- Every command requires root unless the hidden test-only override is used.
- Configuration and role files are parsed as data and are never sourced as shell code.
- Unknown or duplicate options fail closed.
- `build` and update commands do not mutate the system without `--apply`.
- `all` follows installed roles instead of adding unrelated services.
- Locks and logs reject symbolic links, unsafe ownership, multiple hard links, and writable parent directories.
- Service configuration is validated before a restart is considered successful.
- A final `hostpanel-doctor --quiet` check is required after applied maintenance.
