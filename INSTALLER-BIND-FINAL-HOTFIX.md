# BIND configuration repair — final transactional hotfix

## Confirmed failure

The affected HostPanel release added this include to `/etc/bind/named.conf`:

```text
include "/etc/bind/hostpanel-hardening.conf";
```

The included file then declared another global `options { ... };` block. Debian's
stock `/etc/bind/named.conf.options` already contains the one global `options`
block BIND permits, so `named-checkconf` stopped with:

```text
/etc/bind/hostpanel-hardening.conf:2: 'options' redefined near 'options'
```

Repeated installation attempts recreated the invalid file and include.

## What the corrected release does

The installer now runs `tools/bind_authoritative_config.py` before starting
BIND. The editor:

1. Refuses symlinked or non-regular BIND configuration files.
2. Takes a complete transaction backup of `/etc/bind`.
3. Removes every legacy HostPanel include from the standard Debian BIND entry
   files.
4. Deletes `/etc/bind/hostpanel-hardening.conf`.
5. Parses `/etc/bind/named.conf.options` while ignoring comments and quoted
   strings.
6. Requires exactly one top-level `options` block; ambiguous configurations are
   refused instead of guessed at.
7. Removes only top-level `recursion`, `allow-recursion`, and
   `allow-query-cache` directives left by earlier HostPanel attempts.
8. Adds exactly one `recursion no;` to the existing distribution block.
9. Preserves unrelated options, comments, ownership, and file mode.
10. Writes changes atomically.
11. Runs both `named-checkconf` and `named-checkconf -z` with the server's own
    BIND version.
12. Restores all original files byte-for-byte if either validation fails.
13. Restores the complete DNS configuration if any later reinstall stage fails.

The old second `options` block is never generated again.

## Repair an already failed server

Use a fresh extraction of the corrected release and run the dedicated repair:

```bash
sudo bash repair-bind-hostpanel.sh
```

The repair script creates a permanent backup under `/root`, validates the full
configuration and all primary zones, restarts `named.service`, and restores the
previous files if validation or startup fails.

After a successful repair, resume the interrupted installation:

```bash
sudo env HP_PANEL_HOST=panel.example.com \
  bash install.sh --reinstall --check

sudo env HP_PANEL_HOST=panel.example.com \
  bash install.sh --reinstall
```

Replace `panel.example.com` with the existing panel hostname.

## Manual inspection

```bash
sudo grep -RIn 'hostpanel-hardening.conf\|^[[:space:]]*options[[:space:]]*{' /etc/bind
sudo named-checkconf
sudo named-checkconf -z
sudo systemctl status named --no-pager -l
```

A corrected Debian installation should not contain
`/etc/bind/hostpanel-hardening.conf`, and the active include tree should contain
only the distribution's global `options` block.

## Logs and backups

- Installer log: `/var/log/hostpanel-install.log`
- Direct repair log: `/var/log/hostpanel-bind-repair.log`
- Direct repair backup: `/root/hostpanel-bind-before-repair-*.tar.gz`
- Reinstall snapshot: path recorded in `/etc/hostpanel/last-reinstall-snapshot`
