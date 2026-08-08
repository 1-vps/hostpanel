# Package-manager prerequisite hotfix

## Failure addressed

The earlier bootstrap installed a cross-distribution package list in one
transaction. On an otherwise supported host, one unavailable optional package
could fail the whole transaction and hide the actual package-manager message
behind `Could not install package-manager prerequisites`.

## Changes

- Debian/Ubuntu bootstrap: `ca-certificates`, `curl`, and `gnupg`.
- `software-properties-common` is installed only for Ubuntu web-role installs
  that enable the external multi-PHP repository.
- `lsb-release` is optional; repository codenames are read from
  `/etc/os-release`.
- Rocky/Alma bootstrap: `ca-certificates`, `curl`, `gnupg2`, and
  `dnf-plugins-core`.
- Removed the RHEL dependency on `redhat-lsb-core`.
- Added apt/dnf lock, network, and metadata retries.
- Failed operations now identify the package set and print the last 50 lines of
  `/var/log/hostpanel-installer/install.log`.
- Corrected the root invocation hint to use the locally extracted installer.

## Validation

- `bash -n install.sh` passed.
- Installer OS/package-layer suite: 56 passed.
- Simulated Debian, Ubuntu, and Rocky bootstrap paths passed.
- Simulated package-manager failure made exactly three attempts and printed the
  underlying log plus recovery commands.
- Extracted TAR.GZ preflight passed on Debian 13 with the control role.
- ZIP/TAR/TAR.GZ path, content, size, and mode parity passed.
- 412 source files and 72 executable files verified.
- Five Ed25519 signatures verified.
- A second build produced byte-identical release artifacts and signatures.

A real root package installation on every supported VM image remains an
infrastructure-dependent acceptance test and is not claimed here.
