# HostPanel CustomBuild optional components

The fresh HostPanel installation remains unchanged:

```text
webserver=nginx_apache
dns=bind
mongodb=off
varnish=off
```

MongoDB and Varnish are explicit post-install choices. Changing an option only updates `/etc/hostpanel/build.conf`; packages and services are changed only by a matching `build ... --apply` command.

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
- accepts Ubuntu 20.04, 22.04 or 24.04, Debian 12, or RHEL-compatible 8/9;
- configures MongoDB's official 8.0 HTTPS repository;
- verifies the downloaded repository key against the pinned full fingerprint `4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05`;
- snapshots MongoDB configuration and repository files before changes;
- installs `mongodb-org`;
- binds `mongod` to `127.0.0.1` only;
- enables `security.authorization`;
- verifies the service, listener and local `mongosh` ping;
- records the applied state in `/etc/hostpanel/mongodb-mode`.

Ubuntu 26.04, Debian 13, ARM64 and other unsupported combinations stop before repository or service mutation.

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

`mongodb=off` stops and disables `mongod`. It deliberately keeps installed packages, repository configuration and database files so an accidental toggle is not destructive. Remove data only through a separate backup-and-decommission procedure.

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

- permits purge requests only from loopback;
- passes requests with `Authorization` or `Cookie` headers;
- passes non-GET/HEAD requests;
- passes common login, admin and API paths;
- does not cache responses with `Set-Cookie`, private/no-cache/no-store policy, or server errors;
- adds `X-HostPanel-Cache: HIT|MISS` for diagnostics;
- uses short grace and keep windows for resilience.

Varnish listens only on `127.0.0.1:6081`; its management interface listens only on `127.0.0.1:6082`. nginx remains the public certificate endpoint.

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

A direct webserver-mode change is rejected while `/etc/hostpanel/varnish-mode` says `on`. This prevents new vhosts from being routed through a cache whose backend still targets the previous origin.

### Disable Varnish

```bash
sudo hostpanel-build set varnish off
sudo hostpanel-build build varnish --apply
sudo hostpanel-build validate varnish
```

The transaction restores direct nginx-to-origin proxy routes, validates and reloads nginx, stops/disables Varnish, and records `off`. If a Varnish enable or validation step fails, HostPanel restores direct origin routes before stopping the cache service.

## Health and recovery

```bash
sudo hostpanel-build options
sudo hostpanel-build versions
sudo hostpanel-build validate all
sudo hostpanel-build doctor
```

Configuration snapshots are written under `/var/backups/hostpanel/custombuild/`; command logs are written to `/var/log/hostpanel-build.log`. `hostpanel-doctor` reads the applied MongoDB and Varnish mode files and expects only the enabled optional services.
