# HostPanel 3.4.0-hardened-r6

`3.4.0-hardened-r6` is a reviewed repository hotfix revision built from the
verified, signed `3.4.0-hardened-r5` source archive. The bootstrap continues to
verify the r5 archive checksum and detached signature before applying the r6
repair chain from the same pinned Git commit. The installed `VERSION` is then
promoted to `3.4.0-hardened-r6` only when its `3.4.0` core matches the signed
base.

## Installation fixes

- Build the Python virtual environment directly at its final versioned path so
  `uvicorn` and other console-script shebangs never reference a deleted staging
  directory.
- Preserve transactional rollback for existing versioned runtimes and legacy
  `/opt/hostpanel/venv` directories.
- Quote the PostgreSQL 17 reserved `users.system_user` identifier during schema
  migration and normal queries.
- Send the configured panel hostname during authenticated readiness probes and
  log non-ready response bodies for actionable diagnostics.
- Keep the privileged-file manifest compatible with both the reviewed installer
  parser and the signed-base runtime readiness validator.
- Make anti-DDoS Nginx generation reinstall-safe: retain shared-zone definitions,
  remove only the redundant `server_tokens` directive, and stop regenerating it.
- Print `HP_EXTERNAL_URL` as the completion URL rather than an IP address that is
  intentionally rejected by trusted-host validation.

## Validation

The release adds regression coverage for final-path virtual environments,
trusted-host readiness, PostgreSQL `system_user`, privileged-manifest runtime
compatibility, anti-DDoS zone preservation, and reviewed version promotion.
The existing installer safety, PostgreSQL 17 schema, Debian 13 Exim/Dovecot, and
OS matrix workflows remain the release gates.

## Upgrade

Pin the reviewed full commit SHA containing this revision and run:

```bash
sudo env \
  HP_REPO_REF=<reviewed-r6-commit-sha> \
  HP_PANEL_HOST=panel.example.com \
  bash bootstrap-install.sh --reinstall --mta exim
```

Administrator accounts and password hashes remain preserved during reinstall.
The panel URL is the configured hostname origin, normally
`https://panel.example.com:2222`.
