# HostPanel documentation index

This is the current documentation map for HostPanel release **3.4.1**.

## Start here

1. [`README.md`](README.md) — product scope, trust model, requirements, and release state
2. [`RELEASE-MANIFEST.json`](RELEASE-MANIFEST.json) — authoritative machine-readable release status
3. [`SETUP.md`](SETUP.md) — authenticated installation from a reviewed commit
4. [`CONFIGURATION.md`](CONFIGURATION.md) — supported configuration controls
5. [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) — mandatory production gates
6. [`SECURITY.md`](SECURITY.md) — security and disclosure policy
7. [`RELEASE-PROCESS.md`](RELEASE-PROCESS.md) — release publication process
8. [`UPDATES.md`](UPDATES.md) — updates and rollback behavior

## Operational references

- [`FIREWALL.md`](FIREWALL.md)
- [`CUSTOMBUILD.md`](CUSTOMBUILD.md)
- [`LOCALIZATION-OVERLAY.md`](LOCALIZATION-OVERLAY.md)
- `.github/workflows/qemu-vm-acceptance.yml`
- `.github/workflows/vps-acceptance.yml`
- `tools/validate-production-vm.sh`
- `tools/validate_release_manifest.py`

## Historical audit material

The following root-level documents describe earlier installer audits and remediation
work. They are retained as historical evidence and **must not be used as the
current release status, deployment approval, or production-readiness source**:

- `AUDIT_COMPLETE.md`
- `INSTALL_SH_BUG_AUDIT.md`
- `SECURITY_AUDIT_SUMMARY.md`
- `QUICK_REFERENCE.md`
- `SIMULATED_TEST_RESULTS.sh`
- older release notes and one-off hotfix reports

Some historical documents contain dates, version numbers, progress checklists, or
phrases such as “ready to deploy” that applied only to their original audit scope.
Current release truth always comes from `RELEASE-MANIFEST.json`, maintained docs,
exact-head workflow results, and production acceptance evidence.

## Consistency enforcement

Run:

```bash
python3 tools/validate_release_manifest.py
```

The validator fails closed when `RELEASE_VERSION`, maintained documentation, and
the release manifest disagree. The same check runs in
`.github/workflows/release-consistency.yml`.
