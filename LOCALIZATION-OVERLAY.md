# HostPanel thirteen-language localization overlay

This commit-addressed overlay extends the signed `3.4.0-hardened-r5` source release from ten to thirteen interface languages.

## Added locales

- Japanese (`ja`)
- Portuguese (`pt`, Brazilian terminology)
- Simplified Chinese (`zh`)

Regional tags are normalized to the supported base locale. Generic and regional Portuguese browser tags resolve to the single Brazilian-terminology `pt` catalog; a separate European Portuguese catalog is not included. Login and panel selectors therefore display **Português (Brasil)**. The Chinese catalog is explicitly Simplified Chinese.

## Existing catalog review

All ten existing catalogs retain their complete key sets. The overlay:

- removes parenthetical singular/plural workarounds from authentication and firewall countdown text
- translates 143 previously source-identical Swedish interface labels and prevents their regression
- uses `nyckel` consistently for Swedish passkey wording in the login flow
- preserves URLs, commands, paths, certificates, identifiers, placeholders, product names, and protocol names

The exact production-correction file layout and Git blobs are regression-locked:

```text
existing-catalog-corrections.json              b0b6a0b6f2176ba5f6c98d3ac6d80dcca5e7c43f
existing-catalog-corrections.sv-ui-1.json      9011dfafc66d79e61e7384b44e8900d64605edc5
existing-catalog-corrections.sv-ui-2.json      a7b45ab2320169c8ad4cffd10956756cc637b2b6
```

## Reviewed new-language correction layers

The checksum-locked compressed bundle remains unchanged. Three reviewed layers are applied after the base catalogs:

1. **Initial high-risk and contamination layer:** 55 values — 19 Japanese, 21 Brazilian Portuguese, and 15 Simplified Chinese.
2. **Semantic final layer:** 182 values — 91 Japanese, 31 Brazilian Portuguese, and 60 Simplified Chinese.
3. **Brazilian Portuguese visible-UI layer:** 80 dialog, validation, and primary-navigation values in `catalog-visible-ui-overrides.pt.json`.

Together they provide **317 explicit reviewed catalog corrections**:

- 110 Japanese values
- 132 Brazilian Portuguese values
- 75 Simplified Chinese values

PR #11 separately supplies ten redesign-owned dynamic dashboard strings for each new locale. Those 30 strings have key and placeholder parity tests, but remain part of native SME sign-off and are not included in the 317 catalog-correction count.

## Trust model

The source archive and signature are not modified. The localization workflow:

1. requires exactly one SHA-256 entry for the signed archive;
2. verifies the archive with the embedded Ed25519 release public key;
3. rejects absolute paths, traversal, links, devices, unsupported member types, excessive member counts, excessive expansion size, and ambiguous top-level roots;
4. extracts into a private directory and reuses one recorded source root for all later steps.

`bootstrap-install.sh` verifies the core overlay, reviewed wrapper, correction files, compressed bundle chunks, catalogs, and documentation against the operator-supplied full Git commit before application.

The reviewed runtime wrapper additionally compares the five semantic final-override files and `catalog-visible-ui-overrides.pt.json` with the selected checkout's exact Git objects before reading JSON. Missing files, symlinks, worktree modifications, uncommitted replacements, and files absent from the selected commit are rejected.

The wrapper enforces:

- initial counts `19/21/15` and SHA-256 `98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604`
- semantic final counts `91/31/60` and SHA-256 `5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f`
- combined initial and semantic counts `110/52/75` and SHA-256 `6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec`
- Portuguese visible-UI count `80` and SHA-256 `193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c`

## Verification

The stacked localization and QEMU workflows check:

- 13 catalogs with exact 2,215-key parity and order
- ten production locales and three release-candidate locales
- non-empty UTF-8 values and placeholder multiplicity
- protected technical tokens, unsafe markup, mojibake, and tracked contamination
- reviewed Swedish labels remain translated
- exact runtime counts and canonical digests
- exact production-correction file layout and Git blobs
- every reviewed key exists in the signed English source and preserves placeholders
- reviewed layers win after the checksum-locked bundle
- `cPanel` and `DirectAdmin` remain intact
- `pt` appears exactly once as `Português (Brasil)`
- Japanese, Brazilian Portuguese, and Simplified Chinese redesign copy remains explicit and placeholder-compatible
- selector, registry, login, Python, JavaScript, Bash, installer, UI, and QEMU integration consistency
- negative tests for locale, count, digest, empty-value, layout, unsafe archive extraction, worktree tampering, and wiring drift

The three new catalogs remain release candidates. The 317 catalog corrections and 30 redesign strings are not equivalent to full native approval. Complete native-speaking subject-matter review remains required before contractual or production-critical use.
