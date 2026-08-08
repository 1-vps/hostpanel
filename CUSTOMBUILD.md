# HostPanel CustomBuild

`hostpanel-build` is HostPanel's DirectAdmin CustomBuild-style maintenance CLI. It provides one root-owned options file, version visibility, component rebuilds, webserver and authoritative-DNS switching, free ACME SSL, and separate system/panel updates.

The **base HostPanel installation always starts in `nginx_apache` web mode and `bind` DNS mode**: nginx is the public HTTP/TLS frontend, Apache is the private backend on `127.0.0.1:8080`, and Bind serves HostPanel's managed zone files. OpenLiteSpeed and PowerDNS are explicit post-install choices.

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
/etc/hostpanel/dns-mode
/etc/hostpanel/ssl/
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

## Authoritative DNS choices

Bind remains the fresh-install default. Choose one authoritative DNS daemon after installation:

```bash
sudo hostpanel-build set dns bind
sudo hostpanel-build set dns powerdns
```

Supported values:

```text
dns=bind|powerdns
```

Review and apply the selected DNS mode:

```bash
sudo hostpanel-build plan dns
sudo hostpanel-build build dns --apply
```

`powerdns` uses PowerDNS Authoritative's BIND backend. HostPanel therefore keeps its existing reviewed BIND-format zone files and managed zone include instead of importing customer records into a second database. On Debian-family systems the build requires `pdns-server` and `pdns-backend-bind`; on RHEL-family systems it requires `pdns`, whose configured package source must contain the BIND backend.

The generated PowerDNS configuration:

- sets `launch=bind` and points `bind-config` to HostPanel's managed zone include;
- enables primary operation and a 60-second zone-file check interval;
- disables the PowerDNS HTTP API and webserver;
- hides the daemon version string;
- grants the `pdns` service read-only supplementary membership in the existing `bind` or `named` group;
- installs `hostpanel-pdns-zones.path`, which runs `pdns_control rediscover` and `pdns_control reload` when the managed include or zone directory changes.

Before port 53 changes ownership, the selected package set and daemon configuration are validated. The previous DNS service is stopped only for the final handoff. If the new daemon fails to start or validate, HostPanel stops it and restores the previously active DNS service. After success, the inactive daemon is disabled and `/etc/hostpanel/dns-mode` records the selected mode.

PowerDNS's BIND backend deliberately keeps its HTTP zone API read-only. HostPanel remains the writer of record and zone files; this avoids two competing sources of DNS truth.

## Free SSL

HostPanel supports free 90-day ACME certificates through either **Let's Encrypt** or **ZeroSSL**. nginx remains the public certificate endpoint in every webserver mode.

Always review the plan before applying:

```bash
sudo hostpanel-build ssl issue example.com \
  --email admin@example.com \
  --www
```

Issue and install a Let's Encrypt certificate:

```bash
sudo hostpanel-build ssl issue example.com \
  --email admin@example.com \
  --provider letsencrypt \
  --www \
  --apply
```

ZeroSSL's ACME service requires External Account Binding credentials. Generate one reusable EAB credential pair in the Developer section of the ZeroSSL dashboard, then save the values with a root-only editor so they do not enter shell history:

```bash
sudo install -d -o root -g root -m 0700 /etc/hostpanel/ssl
sudoedit /etc/hostpanel/ssl/zerossl-eab-kid
sudoedit /etc/hostpanel/ssl/zerossl-eab-hmac
sudo chown root:root \
  /etc/hostpanel/ssl/zerossl-eab-kid \
  /etc/hostpanel/ssl/zerossl-eab-hmac
sudo chmod 0600 \
  /etc/hostpanel/ssl/zerossl-eab-kid \
  /etc/hostpanel/ssl/zerossl-eab-hmac
```

Issue and install a ZeroSSL certificate:

```bash
sudo hostpanel-build ssl issue example.com \
  --email admin@example.com \
  --provider zerossl \
  --www \
  --apply
```

Custom credential paths can be supplied with `--eab-kid-file` and `--eab-hmac-file`. Each file must be a root-owned, single-link regular file with mode `0400` or `0600`.

Check certificate state or renew due certificates:

```bash
sudo hostpanel-build ssl status
sudo hostpanel-build ssl status example.com
sudo hostpanel-build ssl renew
sudo hostpanel-build ssl renew example.com --apply
```

Issuance uses Certbot's nginx authenticator/installer, enables HTTP-to-HTTPS redirect, verifies the resulting certificate lineage, and installs a deploy hook that runs `nginx -t` before reloading nginx. ZeroSSL EAB values are passed to Certbot through a temporary root-only configuration file under `/run/hostpanel-build`; the file is deleted immediately after the issuance process.

When a domain is switched to `apache`, HostPanel preserves the active certificate directives and renders an equivalent port-443 proxy edge. A CustomBuild webserver switch must therefore not remove HTTPS from an already secured domain.

## Other options

```text
database=mariadb|postgresql|both
mta=postfix|exim
dns=bind|powerdns
php_versions=7.4,8.0,8.1,8.2,8.3,8.4,8.5
```

Examples:

```bash
sudo hostpanel-build set php_versions 8.5,8.4,8.3,8.2
sudo hostpanel-build set database both
sudo hostpanel-build set mta postfix
sudo hostpanel-build set dns powerdns
```

## Versions and plans

```bash
sudo hostpanel-build versions
sudo hostpanel-build versions --json
sudo hostpanel-build plan all
sudo hostpanel-build plan web
sudo hostpanel-build plan dns
sudo hostpanel-build validate all
```

Planning and version commands do not change packages or services.

The `all` target reads `/etc/hostpanel/roles.conf`; it only includes components belonging to roles installed on that node.

## Rebuild components

A build without `--apply` prints a plan only:

```bash
sudo hostpanel-build build nginx
sudo hostpanel-build build web
sudo hostpanel-build build dns
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

Every package selected for reinstall must have a current repository candidate, even if an older version is already installed. This preflight completes before snapshots, package transactions, or service restarts.

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
- The base installer never attempts to install OpenLiteSpeed or PowerDNS.
- nginx remains the public edge in every web mode, so TLS and public listener ownership never move between daemons during a switch.
- Existing Certbot TLS directives are retained when Apache-only content handling is selected.
- ZeroSSL EAB secrets are read only from private files and are never placed directly in the executed Certbot argument vector.
- Webserver conversion uses HostPanel's tested per-domain configuration engine instead of editing customer vhosts with broad substitutions.
- Relevant configuration is snapshotted before package realignment.
- Every reinstalled package requires a current candidate before any service mutation.
- Bind and PowerDNS share the reviewed zone-file source of truth; only one daemon owns port 53.
- A failed DNS handoff restores the previously active daemon.
- The PowerDNS HTTP API and webserver remain disabled in BIND-backend mode.
- OpenLiteSpeed package installation is isolated behind a runtime systemd mask; only loopback listeners are accepted.
- Every selected PHP branch requires an executable matching LSPHP runtime before OpenLiteSpeed is activated.
- Multi-domain webserver changes roll back already converted domains in reverse order on failure.
- Unused Apache or OpenLiteSpeed backend services are disabled after successful reconciliation.
- Service configuration is validated before restart.
- Applied maintenance ends with `hostpanel-doctor --quiet`.

## Repository availability

`openlitespeed` and `powerdns` are explicit post-install choices, not base-install dependencies. If the configured repositories do not expose the complete required package set, the corresponding `hostpanel-build build ... --apply` command exits before changing services. Keep `webserver=nginx_apache` and `dns=bind` until all required candidates are visible.
