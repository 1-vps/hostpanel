# Security policy

## Supported versions

Only the newest published hardened release is supported with security fixes.
Older releases should be upgraded before reporting an issue unless the report
is specifically about the upgrade path.

| Release | Supported |
| --- | --- |
| 3.4.0-hardened-r5 and later hardened revisions | Yes |
| Earlier releases | No |

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue, discussion, log,
chat room, or support ticket containing secrets.

Use the repository host's private **Report a vulnerability** / private security
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

## Release verification

Published source archives, update manifests, and checksum files must be signed
with the release key. Verify both the signature and SHA-256 checksum before
installation. See `RELEASE-PROCESS.md`.
