# HostPanel firewall

HostPanel configures either UFW on Debian/Ubuntu or firewalld on Rocky Linux and
AlmaLinux. The installer uses a default-deny policy and preserves administrative
access before committing firewall changes.

## Administrative source

Supply a narrow administrative source with `HP_PANEL_ADMIN_CIDR`:

```text
192.0.2.10/32
2001:db8:100::/64
```

When installation runs over SSH, the client address can be detected from the
session. If neither source is available, installation stops unless
`HP_ALLOW_PUBLIC_PANEL=yes` explicitly accepts public exposure.

## SSH safety

The installer reads active SSH ports from the current connection and `sshd -T`.
Those ports are allowed before the default-deny policy is enabled. Keep provider
console access available throughout installation and reboot testing.

## Timed rollback

Before committing firewall changes, the installer schedules a systemd rollback
that restores the saved firewall configuration after five minutes. A successful
installation cancels the rollback. If the SSH connection is lost, wait for the
rollback or use the provider console rather than repeatedly rerunning the
installer.

## Panel ports

The default public panel port is `2222`. Override it with `HP_PANEL_PORT` only
when the provider firewall and administrative access policy are updated at the
same time.

The panel backend port is intended for local proxying and must not be exposed
publicly. Review listeners with:

```bash
sudo ss -lntup
```

## Role-based rules

Web, mail, DNS, backup, and edge roles can require additional rules. The exact
rules depend on selected roles and MTA. Review the generated firewall rather
than relying on an unchanging static port list.

UFW:

```bash
sudo ufw status verbose
sudo ufw status numbered
```

firewalld:

```bash
sudo firewall-cmd --state
sudo firewall-cmd --list-all
sudo firewall-cmd --list-all --permanent
```

The runtime and permanent firewalld views should agree after installation.

## Reboot verification

Run the production VM validator before reboot, record the boot ID, reboot, then
run its post-reboot mode. Confirm:

- the configured SSH port remains reachable;
- the panel port is reachable only from approved sources;
- the firewall service is active;
- runtime and persistent rules match;
- no failed systemd units remain.

See [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for the complete
acceptance procedure and [`SECURITY.md`](SECURITY.md) for the trust model.
