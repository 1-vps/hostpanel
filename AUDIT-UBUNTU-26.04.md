# Ubuntu 26.04 installer setup audit

**Audit date:** 2026-07-28  
**Target:** Ubuntu 26.04 LTS (Resolute Raccoon)  
**Scope:** \, maintained setup documentation, and installer test matrices

## Changes

- Added Ubuntu 26.04 to the explicit supported-release gate and diagnostics.
- Added Ubuntu 26.04 Docker images to both installer test matrices.
- Added focused regression tests for supported and rejected Ubuntu releases.
- Updated \ with the Ubuntu 26.04 repository policy.
- On Ubuntu 26.04, auto-configured external PHP and Rspamd repositories are disabled and distribution packages are used.

## Repository compatibility findings

1. The Ondrej PHP PPA does not currently publish a Resolute suite. Ubuntu 26.04 supplies native PHP 8.5 FPM and CLI packages, so the installer uses those packages instead.
2. The upstream Rspamd stable APT repository does not currently publish a Resolute suite. Ubuntu 26.04 supplies Rspamd in its archive, so the installer uses the Ubuntu package.
3. Multi-PHP availability on Ubuntu 26.04 is limited to PHP branches published by Ubuntu until a separate reviewed repository source supports Resolute.
4. The basic Docker matrix validates OS detection, prerequisite provisioning, package-manager detection, FQDN rejection, preflight, and dry-run behavior. It does not boot a complete systemd host or activate every service.

## Automated results

| Check | Result |
| --- | --- |
| Bash syntax, focused regression tests, and static matrix | success |
| Ubuntu 26.04 Docker preflight/dry-run matrix | success |
| Ubuntu 26.04 required-package candidate audit | failure |
| ShellCheck error-level audit | success |

## Production validation still required

Before declaring a release production-ready, run a full installation on a disposable Ubuntu 26.04 VM with systemd, reboot it, run \, test all selected roles, and perform a backup/restore test. Container preflight cannot validate service startup, kernel quota behavior, firewall persistence, mail delivery, or DNS delegation end to end.
