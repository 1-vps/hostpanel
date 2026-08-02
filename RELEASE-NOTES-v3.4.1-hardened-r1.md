# HostPanel 3.4.1-hardened-r1 release candidate

Status: **release candidate — not yet signed or published**

Target application version: `3.4.1`

Release-line base commit:

```text
bd3486c98eb0def4dba4aa645eee9f9f081e4d90
```

This release line starts from the verified merge of PR #124 and gives the privileged production-validator hardening its own patch version rather than silently shipping it under the existing `3.4.0` version.

## Security changes

- bind validation reports, reboot state, and operator hooks to trusted file descriptors;
- reject unsafe parent directories, links, hardlinks, non-regular files, ownership/mode drift, and pathname replacement;
- execute operator hooks through the validated open descriptor with a sanitized environment;
- create report files as root-owned mode `0600` files below an exact mode `0700` directory;
- replace reboot state atomically through a held directory descriptor with inode/path revalidation and fsync;
- bind provider recovery metadata to the reviewed commit, provider instance, snapshot ID, and UTC creation timestamp;
- add focused race, file-type, replacement, and report-directory mode regressions.

## Verified evidence for the merged security change

Exact PR head:

```text
d30b63e804c5cec1aa75f7cf14ee2a3085e16e70
```

- Production VM validation harness run `30750537350`: passed.
- Installer hardening run `30750537323`: all 11 jobs passed, including all supported OS preflights and the Ubuntu 26.04/Python 3.14 runtime lock.
- QEMU VM acceptance run `30750537336`: passed real boot, installation, pre-reboot validation, reboot-state preparation, real reboot, post-reboot validation, sanitization, sealing, and upload.
- QEMU evidence artifact ID `8834409741`.
- GitHub artifact ZIP digest:

```text
sha256:9e289a569f3be108735fed9930e7ab4f4e6aeadd7f00b22dfd7a0c9b8bcf36f0
```

The sealed archive contained 14 deterministic root-owned mode-`0600` regular files, no links or unsafe paths, no detected token/private-key/password patterns, and no validator failures.

Validator totals:

- pre-reboot: 53 PASS, 2 WARN, 0 FAIL;
- prepare-reboot: 54 PASS, 2 WARN, 0 FAIL;
- post-reboot: 54 PASS, 2 WARN, 0 FAIL.

## Required before merge/publication

The repository's published semantic version is derived from the VERSION file inside the signed source archive. The current signed source archive still identifies `3.4.0`; therefore this release candidate must not be merged or tagged until all of the following are complete:

1. Build a deterministic `3.4.1` source archive from the reviewed release commit.
2. Verify that the archive contains exactly one canonical `VERSION` with `3.4.1`.
3. Sign the exact source archive bytes with the protected Ed25519 release key in the protected release environment.
4. Replace the prior source archive/checksum/signature set atomically and verify it against `releases/update.pub`.
5. Update QEMU, validator, documentation, and release tests to expect `3.4.1`.
6. Run the complete installer, production-harness, UI/localization, and real QEMU acceptance suites on the final release-candidate head.
7. Complete the external provider-backed acceptance and native-language signoff gates.
8. Publish only through `.github/workflows/publish-release.yml` after every fail-closed release issue is closed.

Open publication gates remain #7, #14, #115, #116, #118, #119, #120, and #121.
