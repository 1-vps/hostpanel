# HostPanel release process

This process defines the minimum evidence for publishing a hardened HostPanel
release. Release signing keys and private build credentials must never be stored
in the repository or CI logs.

## 1. Select the release source

- start from a reviewed commit with a clean working tree;
- record the full 40-character Git commit SHA;
- ensure the installer hardening and production-VM harness workflows pass;
- confirm the documented version and supported operating systems.

## 2. Build the source archive

Create a deterministic source archive with one top-level directory named after
the release. Include the application, `VERSION`, and the hash-locked Python
requirements file. Exclude Git metadata, local credentials, logs, caches, test
artifacts, and customer data.

The archive name must follow the repository's source-release naming convention,
for example:

```text
hostpanel-v3.4.0-hardened-r6-source.tar.gz
```

## 3. Generate checksums

Generate a SHA-256 checksum entry for the exact archive bytes:

```bash
sha256sum hostpanel-v3.4.0-hardened-r6-source.tar.gz > SHA256SUMS
```

`SHA256SUMS` must identify exactly one HostPanel source archive for the bootstrap
verification path.

## 4. Sign the archive

Sign the archive with the protected Ed25519 release private key and publish the
raw signature beside the archive. The bootstrap trust root is embedded in the
reviewed script; do not replace it with a public key fetched from the same
untrusted commit being verified.

Verify the signature and checksum independently before publishing.

## 5. Validate the installer overlay

The reviewed Git commit must contain:

```text
install.sh
install.base.sh
tools/harden_install.py
tools/harden_install_runtime.py
```

The bootstrap verifies each file against its Git object. The launcher also
verifies the preserved base installer by Git blob ID before deriving the
hardened installer.

## 6. Run release gates

Required automated evidence:

- deterministic installer generation;
- Bash syntax and ShellCheck error-level checks;
- installer and security regression tests;
- supported-OS preflight matrix;
- source archive checksum and anchored signature verification;
- Ubuntu 26.04/Python 3.14 locked-runtime installation;
- production VM validation harness syntax, tests, and ShellCheck.

Required external evidence on a disposable systemd VM:

- complete installation on the target OS;
- pre- and post-reboot validation;
- selected-role service checks;
- external web, DNS, and mail tests;
- backup and restore;
- failure-injection rollback test;
- provider-level recovery confirmation.

## 7. Publish and document

Update `README.md`, `SETUP.md`, and `PRODUCTION_READINESS.md` to the same validated
full commit SHA. Publish the archive, signature, and checksum file together.
Retain the workflow run identifiers and external validation evidence.

Do not describe a release as production-ready while required systemd-VM evidence
is incomplete.

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting and
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for acceptance steps.
