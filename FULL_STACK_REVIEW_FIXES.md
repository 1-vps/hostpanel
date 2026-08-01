# Full-stack review fixes

This branch addresses the security, acceptance, and UI integration findings from the review of PRs #15, #11, and #12.

Implemented safeguards:

- QEMU repository credentials are de-exported before the long-lived logging process starts.
- Bootstrap and installer launchers terminate and reap active installer children on HUP, INT, and TERM before cleanup.
- The provider acceptance workflow installs an operator-supplied full commit SHA, runs only from `main`, and keeps provider secrets out of job-wide environment state.
- External provider probes fail closed for DNS, trusted HTTPS, and required public listeners.
- Production VM validation treats an installed but inactive OpenLiteSpeed service as a failure.
- Dashboard quick actions and rail links resynchronize when navigation permissions change asynchronously.
- Dynamic dashboard copy follows the selected locale, including Japanese, Brazilian Portuguese, and Simplified Chinese.
- Browser coverage now includes asynchronous permission changes, release-candidate locale switching, and screenshots after scrolling the internal content container.
- Regression coverage locks the token, signal, provider workflow, OpenLiteSpeed, and UI behavior.

Repository environment configuration remains an operational requirement: the `vps-acceptance` environment must permit protected `main` deployments only and require independent approval before releasing secrets.
