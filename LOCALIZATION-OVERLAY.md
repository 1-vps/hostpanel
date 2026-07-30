# HostPanel thirteen-language localization overlay

This commit-addressed overlay extends the signed `3.4.0-hardened-r5` source release from ten to thirteen interface languages.

## Added locales

- Japanese (`ja`)
- Portuguese (`pt`)
- Simplified Chinese (`zh`)

All regional tags are normalized to the supported base locale. The Portuguese catalog uses region-neutral wording, and the Chinese catalog is explicitly Simplified Chinese.

## Existing catalog review

All ten existing catalogs retain their complete key sets. The overlay:

- removes parenthetical singular/plural workarounds from authentication and firewall countdown text
- repairs genuine source-identical Swedish interface labels
- uses `nyckel` consistently for Swedish passkey wording in the login flow
- preserves URLs, commands, paths, certificates, identifiers, placeholders, product names, and protocol names

## Trust model

The original source archive and its signature are not modified. `bootstrap-install.sh` first verifies that signed archive, then verifies every localization overlay file against the operator-supplied full Git commit before applying it to the extracted source tree. The overlay remains source-visible in the repository so catalog and terminology changes can be reviewed independently. Both the original localization audit and the stricter editorial audit must pass before installation can begin.

## Verification

The localization workflow checks:

- 13 catalogs with exact 2,215-key parity and order
- non-empty UTF-8 values
- placeholder multiplicity
- protected technical tokens
- unsafe markup and mojibake
- selector, registry, login, Python, JavaScript, and Bash syntax consistency

The three new catalogs were produced with language-specific machine assistance, protected-token reconstruction, and editorial overrides. Specialized legal, billing, tax, contractual, and jurisdiction-specific compliance language still requires native-speaking subject-matter review before contractual use.
