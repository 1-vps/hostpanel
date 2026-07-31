# HostPanel thirteen-language localization overlay

This commit-addressed overlay extends the signed `3.4.0-hardened-r5` source release from ten to thirteen interface languages.

## Added locales

- Japanese (`ja`)
- Portuguese (`pt`)
- Simplified Chinese (`zh`)

All regional tags are normalized to the supported base locale. The `pt` catalog uses Brazilian Portuguese administrative terminology; generic and regional Portuguese browser tags currently resolve to that single catalog. The Chinese catalog is explicitly Simplified Chinese.

## Existing catalog review

All ten existing catalogs retain their complete key sets. The overlay:

- removes parenthetical singular/plural workarounds from authentication and firewall countdown text
- translates 143 previously source-identical Swedish interface labels and prevents their regression
- uses `nyckel` consistently for Swedish passkey wording in the login flow
- preserves URLs, commands, paths, certificates, identifiers, placeholders, product names, and protocol names

## Reviewed new-language correction layers

The checksum-locked compressed bundle remains unchanged. Two source-visible review layers are applied after the base catalogs:

1. **Initial high-risk and contamination layer:** 55 values — 19 Japanese, 21 Brazilian Portuguese, and 15 Simplified Chinese.
2. **Semantic final layer:** 182 values — 91 Japanese, 31 Brazilian Portuguese, and 60 Simplified Chinese.

Together they provide **237 explicit reviewed corrections**:

- 110 Japanese values
- 52 Brazilian Portuguese values
- 75 Simplified Chinese values

The semantic layer corrects meaning-changing errors in destructive confirmations, credentials, passwords, subscriptions, billing, backup and restore, migration, rollback, disaster recovery, DNS, firewall, mail, database, staging, OpenLiteSpeed, account-management copy, dialogs, route labels, status messages, and operational feedback. It is split into five exact files and applied by `apply_localization_overlay_reviewed.py` after the locked bundle.

## Trust model

The original source archive and its signature are not modified. `bootstrap-install.sh` first verifies that signed archive, then verifies every localization overlay file against the operator-supplied full Git commit before applying it to the extracted source tree. The compressed bundle still requires exactly 63,168 Base64 bytes and its existing SHA-256.

The runtime wrapper independently enforces all source-visible review contracts before writing any catalog:

- initial layer counts `19/21/15` and SHA-256 `98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604`
- semantic final counts `91/31/60` and SHA-256 `5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f`
- combined counts `110/52/75` and SHA-256 `6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec`

It also rejects missing, extra, unsafe, malformed, empty, duplicate, or overlapping review files and values. These runtime checks remain effective even when external CI is unavailable.

## Verification

The localization workflow checks:

- 13 catalogs with exact 2,215-key parity and order
- ten production locales and three release-candidate locales
- non-empty UTF-8 values and placeholder multiplicity
- protected technical tokens, unsafe markup, and mojibake
- reviewed Swedish interface labels remain translated
- all three runtime counts and canonical digests
- every reviewed key exists in the signed English source and preserves its placeholders
- the semantic final layer wins after the checksum-locked compressed bundle
- the reconstructed Brazilian Portuguese catalog contains no known cross-language residue
- selector, registry, login, Python, JavaScript, Bash, installer, and QEMU integration consistency
- bootstrap verifies every one of the five semantic final-override files against its Git object
- negative runtime tests reject locale, count, digest, empty-value, and wiring drift

The three new catalogs remain release candidates. Specialized legal, billing, tax, contractual, security, and jurisdiction-specific compliance language still requires native-speaking subject-matter review before contractual use.
