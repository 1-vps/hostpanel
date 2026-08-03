# HostPanel CustomBuild

`hostpanel-build` is HostPanel's DirectAdmin CustomBuild-style maintenance CLI. It provides one root-owned options file, version visibility, component rebuilds, webserver switching, and separate system/panel updates.

The **base HostPanel installation always starts in `nginx_apache` mode**: nginx is the public HTTP/TLS frontend and Apache is the private backend on `127.0.0.1:8080`. OpenLiteSpeed is not installed by the base installer.

## Install

From a reviewed HostPanel source tree:

```bash
sudo bash tools/install-hostpanel-build.sh
```

This installs:

```text
/usr/local/sbin/hostpanel-build
/opt/hostpanel/tools/hostpanel-build
/etc/hostpanel/build.conf
/etc/hostpanel/webserver-mode
```

## Webserver choices

Show the current options:

```bash
sudo hostpanel-build options
```

Choose exactly one webserver mode:

```bash
sudo hostpanel-build set webserver nginx_apache
sudo hostpanel-build set webserver nginx
sudo hostpanel-build set webserver apache
sudo hostpanel-build set webserver openlitespeed
```

Supported values:

```text
webserver=nginx_apache|nginx|apache|openlitespeed
```

The modes mean:

- `nginx_apache`: nginx terminates public HTTP/TLS and serves static files; Apache handles dynamic requests and `.htaccess` on `127.0.0.1:8080`.
- `nginx`: nginx terminates public HTTP/TLS and serves all content through PHP-FPM where required.
- `apache`: nginx remains a thin public HTTP/TLS edge, but every customer request is proxied to Apache on `127.0.0.1:8080`; Apache therefore handles all customer content and `.htaccess` without competing for ports 80 or 443.
- `openlitespeed`: nginx remains the public edge while OpenLiteSpeed serves each domain through the private `127.0.0.1:8088` backend.

Changing the option alone does not touch services. Review the plan, then apply it:

```bash
sudo hostpanel-build plan web
sudo hostpanel-build build web --apply
```

An applied web build installs or realigns the required packages, validates configurations, changes every managed domain through HostPanel's existing webserver engine, records the mode for future domains, disables unused backend services, and runs `hostpanel-doctor`.

For `openlitespeed`, every selected PHP branch must also be available as a matching LSPHP runtime and extension set. The switch refreshes package metadata and checks every required `openlitespeed`/`lsphp*` candidate before masking or restarting a service. If the configured repositories do not publish the complete set, the command stops before service mutation.

During an OpenLiteSpeed switch, `lsws.service` is runtime-masked while packages are installed. HostPanel then forces WebAdmin to `127.0.0.1:7080`, installs the HostPanel listener on `127.0.0.1:8088`, enables proxy-IP handling, validates all LSPHP binaries, and converts managed domains transactionally. Failed domain conversion is rolled back in reverse order.

## Other options

```text
database=mariadb|postgresql|both
mta=postfix|exim
php_versions=7.4,8.0,8.1,8.2,8.3,8.4,8.5
```

Examples:

```bash
sudo hostpanel-build set php_versions 8.5,8.4,8.3,8.2
sudo hostpanel-build set database both
sudo hostpanel-build set mta postfix
```

## Versions and plans

```bash
sudo hostpanel-build versions
sudo hostpanel-build versions --json
sudo hostpanel-build plan all
sudo hostpanel-build plan web
sudo hostpanel-build validate all
```

Planning and version commands do not change packages or services.

The `all` target reads `/etc/hostpanel/roles.conf`; it only includes components belonging to roles installed on that node.

## Rebuild components

A build without `--apply` prints a plan only:

```bash
sudo hostpanel-build build nginx
sudo hostpanel-build build web
sudo hostpanel-build build all
```

Apply explicitly:

```bash
sudo hostpanel-build build nginx --apply
sudo hostpanel-build build php --apply
sudo hostpanel-build build database --apply
sudo hostpanel-build build mail --apply
sudo hostpanel-build build dns --apply
sudo hostpanel-build build all --apply
```

Configuration snapshots are stored under `/var/backups/hostpanel/custombuild/` and execution details under `/var/log/hostpanel-build.log`.

## Updates

Check or apply operating-system package updates:

```bash
sudo hostpanel-build update_versions
sudo hostpanel-build update_versions --apply
```

Check or apply the next signed HostPanel release:

```bash
sudo hostpanel-build update_panel
sudo hostpanel-build update_panel --apply
```

`update_panel` uses the existing signed manifest, archive checksum, signature and trusted-key verification.

## Safety model

- Configuration and role files are parsed as data and are never sourced as shell code.
- Unknown, duplicate or unsupported options fail closed.
- Mutating commands require root, a lock and explicit `--apply`.
- The base installer never attempts to install OpenLiteSpeed.
- nginx remains the public edge in every mode, so TLS and public listener ownership never move between daemons during a switch.
- Webserver conversion uses HostPanel's tested per-domain configuration engine instead of editing customer vhosts with broad substitutions.
- Relevant configuration is snapshotted before package realignment.
- OpenLiteSpeed package installation is isolated behind a runtime systemd mask; only loopback listeners are accepted.
- Every selected PHP branch requires an executable matching LSPHP runtime before OpenLiteSpeed is activated.
- Multi-domain webserver changes roll back already converted domains in reverse order on failure.
- Unused Apache or OpenLiteSpeed backend services are disabled after successful reconciliation.
- Service configuration is validated before restart.
- Applied maintenance ends with `hostpanel-doctor --quiet`.

## OpenLiteSpeed repository availability

`openlitespeed` is an explicit post-install choice, not a base-install dependency. On a distribution where the configured official repositories do not expose `openlitespeed` and every required `lsphp*` package, `hostpanel-build build web --apply` exits before changing services. This includes Ubuntu releases for which LiteSpeed has not published a complete package set. Keep `webserver=nginx_apache` until the required candidates are visible.
