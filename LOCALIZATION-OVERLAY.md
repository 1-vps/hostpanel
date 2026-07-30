# HostPanel thirteen-language localization overlay

This commit-addressed overlay extends the signed `3.4.0-hardened-r5` source release from ten to thirteen interface languages.

## Added locales

- Japanese (`ja`)
- Portuguese (`pt`)
- Simplified Chinese (`zh`)

All regional tags are normalized to the supported base locale. The `pt` catalog now uses consistent Brazilian Portuguese administrative terminology; generic and regional Portuguese browser tags currently resolve to that single catalog. The Chinese catalog is explicitly Simplified Chinese.

## Existing catalog review

All ten existing catalogs retain their complete key sets. The overlay:

- removes parenthetical singular/plural workarounds from authentication and firewall countdown text
- translates 143 previously source-identical Swedish interface labels and prevents their regression
- uses `nyckel` consistently for Swedish passkey wording in the login flow
- preserves URLs, commands, paths, certificates, identifiers, placeholders, product names, and protocol names

## High-risk new-language review

A focused editorial and contamination pass corrected 55 strings before native sign-off:

- 19 Japanese strings
- 21 Brazilian Portuguese strings
- 15 Simplified Chinese strings

The reviewed scope covers destructive confirmations, identity and authentication, encrypted credentials, subscriptions and billing, compliance evidence, backup and restore, disaster recovery, firewall rollback, OpenLiteSpeed migration, migration confirmation tokens, and Portuguese language contamination. A dedicated regression test locks the exact reviewed values, verifies placeholder parity against the English catalog in the signed source archive, reconstructs the final Portuguese catalog, and rejects known Spanish, Italian, French, and European Portuguese residue.

## Trust model

The original source archive and its signature are not modified. `bootstrap-install.sh` first verifies that signed archive, then verifies every localization overlay file against the operator-supplied full Git commit before applying it to the extracted source tree. The overlay remains source-visible in the repository so catalog and terminology changes can be reviewed independently. Both the original localization audit and the stricter editorial audit must pass before installation can begin.

## Verification

The localization workflow checks:

- 13 catalogs with exact 2,215-key parity and order
- ten production locales and three release-candidate locales
- non-empty UTF-8 values
- placeholder multiplicity
- protected technical tokens
- unsafe markup and mojibake
- reviewed Swedish interface labels remain translated
- exact counts, values, and placeholders for the 55 high-risk and contamination overrides
- the reconstructed Portuguese catalog contains no known cross-language residue
- selector, registry, login, Python, JavaScript, and Bash syntax consistency

The three new catalogs were produced with language-specific machine assistance, protected-token reconstruction, and editorial overrides. Specialized legal, billing, tax, contractual, and jurisdiction-specific compliance language still requires native-speaking subject-matter review before contractual use.
