# HostPanel CustomBuild optional components

The fresh HostPanel installation remains unchanged:

```text
webserver=nginx_apache
dns=bind
mongodb=off
varnish=off
```

MongoDB and Varnish are explicit post-install choices. Changing an option only updates `/etc/hostpanel/build.conf`; packages and services are changed only by a matching `build ... --apply` command. Files under `/etc/hostpanel/*-mode` record the last successfully applied runtime state and are not overwritten merely because a different option has been selected.

## MongoDB Community 8.0

Enable MongoDB:

```bash
sudo hostpanel-build set mongodb 8.0
sudo hostpanel-build plan mongodb
sudo hostpanel-build build mongodb --apply
sudo hostpanel-build validate mongodb
```

The CustomBuild path:

- accepts only x86_64 hosts;
- accepts Ubuntu 22.04 or 24.04, Debian 12, or Rocky/AlmaLinux 9;
- configures MongoDB's official 8.0 HTTPS repository;
- verifies that the downloaded repository-key payload contains exactly the pinned primary key fingerprint `4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05`;
- installs the verified local keyring for both APT and DNF instead of asking the package manager to fetch the key again;
- snapshots MongoDB configuration and repository files before changes;
- runtime-masks `mongod.service` while package scripts and hardening run;
- installs `mongodb-org`;
- binds `mongod` to `127.0.0.1` only;
- enables `security.authorization`;
- verifies the service, loopback listener and a real local `mongosh` connection without requiring an anonymous database command;
- records the applied state in `/etc/hostpanel/mongodb-mode` only after validation succeeds.

Ubuntu 26.04, Debian 13, Rocky/AlmaLinux 10, ARM64 and other unsupported combinations stop before repository or service mutation. Ubuntu 20.04 and RHEL/CentOS 8 are not accepted because they are outside HostPanel's supported host-platform set.

If installation, configuration, service startup, disablement or validation fails, HostPanel restores the previous applied-mode file and the service's prior active/inactive and enabled/disabled state. Package-script side effects are still only best-effort reversible.

### Create the first administrator

MongoDB's localhost exception is available only before the first user or role exists. Immediately after the first successful build, connect locally and create the administrator:

```bash
mongosh --host 127.0.0.1
```

Then create the user in the `admin` database, using a strong unique password:

```javascript
use admin
db.createUser({
  user: "hostpanel-admin",
  pwd: passwordPrompt(),
  roles: [{ role: "root", db: "admin" }]
})
```

Exit and verify authenticated access:

```bash
mongosh --host 127.0.0.1 \
  --authenticationDatabase admin \
  --username hostpanel-admin \
  --password
```

Do not expose port 27017 through the firewall or replace the loopback bind without a separately reviewed network and authentication design.

### Disable MongoDB

```bash
sudo hostpanel-build set mongodb off
sudo hostpanel-build build mongodb --apply
sudo hostpanel-build validate mongodb
```

`mongodb=off` stops and disables `mongod`. It deliberately keeps installed packages, repository configuration and database files so an accidental toggle is not destructive. If the disable transaction or applied-state write fails, the previous runtime and boot state is restored. Remove data only through a separate backup-and-decommission procedure.

## Varnish Cache

Varnish is supported with these webserver modes:

```text
nginx_apache
apache
openlitespeed
```

It is not enabled with pure `webserver=nginx`, because that mode currently has no separate private nginx origin listener.

Enable Varnish:

```bash
sudo hostpanel-build set varnish on
sudo hostpanel-build plan varnish
sudo hostpanel-build build varnish --apply
sudo hostpanel-build validate varnish
```

The resulting topology is:

```text
client -> nginx public HTTP/TLS edge -> 127.0.0.1:6081 Varnish
       -> 127.0.0.1:8080 Apache
```

For OpenLiteSpeed mode, the origin is `127.0.0.1:8088` instead.

The generated VCL:

- rejects every HTTP `PURGE` request with status 405; cache administration uses the authenticated local CLI instead;
- passes requests with `Authorization` or `Cookie` headers;
- passes non-GET/HEAD requests;
- passes common login, admin and API paths;
- does not cache responses with `Set-Cookie`, private/no-cache/no-store policy, or server errors;
- adds `X-HostPanel-Cache: HIT|MISS` for diagnostics;
- uses short grace and keep windows for resilience.

Varnish listens only on `127.0.0.1:6081`; its management interface listens only on `127.0.0.1:6082`. nginx remains the public certificate endpoint. Management always uses `-S` pre-shared-key authentication. When no secret exists, HostPanel generates a cryptographically random secret as `0640 root:<varnish-group>`. Existing secrets must be root-owned, single-linked, 0600 root-only or 0640 for the Varnish service group, and contain a bounded non-empty value.

Package installation runs while `varnish.service` is runtime-masked. HostPanel validates the VCL, both loopback listeners, systemd state, nginx proxy configuration, exact secret path in the systemd drop-in and an authenticated `varnishadm ping` before considering the change applied.

### Webserver changes while Varnish is active

Disable the active Varnish routing before changing the global webserver mode:

```bash
sudo hostpanel-build set varnish off
sudo hostpanel-build build varnish --apply

sudo hostpanel-build set webserver openlitespeed
sudo hostpanel-build build web --apply

sudo hostpanel-build set varnish on
sudo hostpanel-build build varnish --apply
```

A direct webserver-mode change is rejected while `/etc/hostpanel/varnish-mode` says `on`. This prevents new vhosts from being routed through a cache whose backend still targets the previous origin. Invalid applied-mode files fail closed rather than being interpreted as `off`.

### Disable Varnish

```bash
sudo hostpanel-build set varnish off
sudo hostpanel-build build varnish --apply
sudo hostpanel-build validate varnish
```

The transaction restores direct nginx-to-origin proxy routes, validates and reloads nginx, stops/disables Varnish, and records `off`. If enablement, disablement or final validation fails, HostPanel restores the prior VCL, systemd drop-in, management secret, applied mode, nginx routing and the service's previous active/inactive and enabled/disabled state.

## Transaction and recovery model

A `build` command is serialized by the HostPanel build lock. Core services, optional MongoDB/Varnish changes and the final doctor checkpoint are one top-level transaction for DNS rollback purposes. If a DNS handoff succeeded but a later optional-component or doctor step fails, HostPanel switches port 53 back to the previously applied DNS daemon and restores the PowerDNS path watcher when it had been active.

Service masking is also fail-closed: if a runtime mask fails after a previously active service was stopped, HostPanel un-masks and restarts that service before returning the error.

Configuration snapshots are written under `/var/backups/hostpanel/custombuild/`; command logs are written to `/var/log/hostpanel-build.log`. Rollback is best-effort because operating-system package scripts are not fully transactional. Retain a provider snapshot and console access for production changes.

## Health and recovery

```bash
sudo hostpanel-build options
sudo hostpanel-build versions
sudo hostpanel-build validate all
sudo hostpanel-build doctor
```

`hostpanel-doctor` reads the applied DNS, MongoDB and Varnish mode files and expects only the enabled services. The production validator checks authoritative DNS on both TCP and UDP port 53 and performs local DNS probes over both transports.
