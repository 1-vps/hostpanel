# HostPanel security

## Supported versions

Only the newest published hardened release is supported with security fixes.
Older releases should be upgraded before reporting an issue unless the report
is specifically about the upgrade path.

| Release | Supported |
| --- | --- |
| `3.4.0-hardened-r6` | Yes |
| Earlier releases | No |

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue, discussion, log,
chat room, or support ticket containing secrets.

Use the repository host's private **Report a vulnerability** or private security
advisory feature. Include:

- affected version and operating system;
- the smallest reproducible request or command sequence;
- expected and observed behaviour;
- impact and required privileges;
- whether credentials, customer data, or signing material may be exposed;
- suggested remediation, when known.

If private vulnerability reporting is unavailable, contact the repository owner
privately through the account or organisation that published the release. The
publisher must configure a private reporting channel before offering HostPanel
to third parties.

## Response targets

The project aims to acknowledge complete reports within 3 business days,
provide an initial severity assessment within 7 business days, and coordinate a
fix and disclosure date with the reporter. These are targets, not a warranty.

## Handling sensitive evidence

Redact API tokens, passwords, private keys, session cookies, customer content,
mail content, database dumps, and real public IP addresses unless they are
strictly necessary. Prefer synthetic test accounts and disposable hosts.

## Installer trust model

The reviewed bootstrap uses two independent verification layers:

1. an embedded long-lived Ed25519 public key authenticates the signed source
   archive;
2. the operator-supplied full Git commit SHA authenticates each executable
   installer overlay against its Git object.

The preserved installer base is checked against its expected Git blob ID before
the deterministic hardener can derive a root installer. A missing, changed, or
ambiguous patch point aborts generation before server mutation.

Use the validated commit documented in [`README.md`](README.md). Never pipe an
unpinned branch URL directly into a root shell.

## External repositories

Automatic mutable third-party repository bootstrap is disabled. The installer
does not execute vendor repository scripts downloaded at installation time.
Preconfigure any required external repository separately, review its key and
configuration, then keep `HP_MULTI_PHP_REPO=off` and `HP_RSPAMD_REPO=off`.

## Administrative access

Panel exposure is fail-closed. Supply a narrow `HP_PANEL_ADMIN_CIDR` or launch
the installer over SSH so the client address can be detected. The explicit
`HP_ALLOW_PUBLIC_PANEL=yes` override is intended only for controlled testing.

The installer detects configured SSH ports, allows them before enabling a
default-deny firewall, and schedules a timed firewall rollback until the
installation commits successfully.

## Secrets and credentials

- generated administrator passwords are passed through standard input rather
  than command-line arguments;
- Redis's unauthenticated default ACL user is disabled;
- a named `hostpanel` Redis ACL user is generated and used by Rspamd;
- installer snapshots and absence manifests are root-owned and separate from
  the panel-managed customer backup tree;
- Rspamd local configuration containing Redis credentials is restricted;
- production validation reports are root-only under `/var/log`.

Do not copy secrets into shell history, issue comments, CI logs, or customer
backup paths.

## Failure handling

Required Redis, Dovecot, PostgreSQL, Apache, Rspamd/MTA, and final
`hostpanel-doctor` failures are fatal. The installer tracks newly installed
packages and managed paths and attempts to restore saved configuration on
failure.

Rollback remains best-effort because package scripts and external service side
effects are not fully transactional. Use a disposable VM for validation and a
provider-level snapshot for production changes.

## Production validation

Run the production VM validator before and after reboot. Its post-reboot mode
requires a changed kernel boot ID. Local checks do not prove external DNS
delegation, TLS trust, mail delivery, reverse DNS, or backup restorability; test
those from separate systems.

See [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for the acceptance
procedure and [`RELEASE-PROCESS.md`](RELEASE-PROCESS.md) for release-signing
requirements.

## Incident response

If an installation behaves unexpectedly:

1. retain provider-console access and do not repeatedly rerun the installer;
2. preserve `/var/log/hostpanel-install.log` and validation reports;
3. record the exact Git commit, archive checksum, OS image, and kernel;
4. inspect `/var/backups/hostpanel-install/` without changing its permissions;
5. restore the provider snapshot when system integrity is uncertain;
6. rotate panel, database, Redis, mail, DNS, and API credentials that may have
   been exposed.
