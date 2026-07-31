# HostPanel thirteen-language localization overlay

This commit-addressed overlay extends the signed `3.4.0-hardened-r5` source release from ten to thirteen interface languages.

## Added locales

- Japanese (`ja`)
- Portuguese (`pt`, Brazilian terminology)
- Simplified Chinese (`zh`)

All regional tags are normalized to the supported base locale. Generic and regional Portuguese browser tags currently resolve to the single Brazilian-terminology `pt` catalog; a separate European Portuguese catalog is not included. The Chinese catalog is explicitly Simplified Chinese.

## Existing catalog review

All ten existing catalogs retain their complete key sets. The overlay:

- removes parenthetical singular/plural workarounds from authentication and firewall countdown text
- translates 143 previously source-identical Swedish interface labels and prevents their regression
- uses `nyckel` consistently for Swedish passkey wording in the login flow
- preserves URLs, commands, paths, certificates, identifiers, placeholders, product names, and protocol names

## Reviewed new-language correction layers

The checksum-locked compressed bundle remains unchanged. Three reviewed layers are applied after the base catalogs:

1. **Initial high-risk and contamination layer:** 55 values — 19 Japanese, 21 Brazilian Portuguese, and 15 Simplified Chinese.
2. **Semantic final layer:** 182 values — 91 Japanese, 31 Brazilian Portuguese, and 60 Simplified Chinese.
3. **Brazilian Portuguese visible-UI layer:** 80 dialog, validation, and primary-navigation values in the source-visible `catalog-visible-ui-overrides.pt.json` file.

Together they provide **317 explicit reviewed corrections**:

- 110 Japanese values
- 132 Brazilian Portuguese values
- 75 Simplified Chinese values

The reviewed layers correct meaning-changing errors in destructive confirmations, credentials, passwords, subscriptions, billing, backup and restore, migration, rollback, disaster recovery, DNS, firewall, mail, database, staging, OpenLiteSpeed, account-management copy, dialogs, route labels, status messages, and operational feedback. Product names such as `cPanel` and `DirectAdmin` remain unchanged.

## Trust model

The original source archive and its signature are not modified. `bootstrap-install.sh` first verifies that signed archive and verifies the core overlay plus all five semantic final-override files against the operator-supplied full Git commit before applying them to the extracted source tree. The compressed bundle still requires exactly 63,168 Base64 bytes and its existing SHA-256.

The Portuguese visible-UI file is read from that selected checkout and independently authenticated by the runtime wrapper through an exact filename, non-symlink requirement, locale/count contract, and canonical SHA-256. This avoids hiding review data inside Python while retaining a deterministic fail-closed content check.

The runtime wrapper enforces the reviewed contracts before writing any catalog:

- initial layer counts `19/21/15` and SHA-256 `98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604`
- semantic final counts `91/31/60` and SHA-256 `5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f`
- combined initial and semantic counts `110/52/75` and SHA-256 `6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec`
- Portuguese visible-UI count `80` and SHA-256 `193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c`

It also rejects missing, extra, unsafe, malformed, empty, duplicate, or overlapping review files and values. These runtime checks remain effective even when external CI is unavailable.

## Verification

The localization workflow checks:

- 13 catalogs with exact 2,215-key parity and order
- ten production locales and three release-candidate locales
- non-empty UTF-8 values and placeholder multiplicity
- protected technical tokens, unsafe markup, and mojibake
- reviewed Swedish interface labels remain translated
- all runtime counts and canonical digests
- every explicitly reviewed key exists in the signed English source and preserves its placeholders
- reviewed layers win after the checksum-locked compressed bundle
- the explicitly reviewed Brazilian Portuguese values contain none of the tracked cross-language contamination markers
- `cPanel` and `DirectAdmin` remain intact in Portuguese navigation
- selector, registry, login, Python, JavaScript, Bash, installer, and QEMU integration consistency
- bootstrap verifies every one of the five semantic final-override files against its Git object
- the visible-UI file has the exact expected name, is not a symlink, and matches the locked digest
- negative runtime tests reject locale, count, digest, empty-value, layout, and wiring drift

The three new catalogs remain release candidates. The 317 corrected values are not equivalent to full-catalog native approval. In particular, the remaining machine-assisted Portuguese base catalog may still contain unreviewed wording or cross-language residue outside the explicitly reviewed subset. Japanese, Brazilian Portuguese, and Simplified Chinese therefore require complete native-speaking subject-matter review before contractual or production-critical use.
