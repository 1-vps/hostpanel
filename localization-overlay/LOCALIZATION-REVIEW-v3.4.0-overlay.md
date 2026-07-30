# Localization review — thirteen-language overlay

## Scope

This overlay retains the ten existing production catalogs and adds Japanese (`ja`), Portuguese (`pt`), and Simplified Chinese (`zh`). All thirteen catalogs contain the same 2,215 stable keys. The single `pt` catalog follows Brazilian Portuguese administrative terminology; generic and regional Portuguese tags currently resolve to it.

## Automated checks

- exact key parity and key order against English
- non-empty UTF-8 string values
- placeholder parity
- unsafe-markup rejection
- source-identical English prose detection with technical allowlists
- panel and login selector coverage
- regional locale normalization
- synchronized server- and client-rendered login messages
- exact canonical digest and placeholder parity for reviewed high-risk overrides
- reconstruction of the final Portuguese catalog and rejection of known Spanish, Italian, French, and European Portuguese residue

## Editorial corrections applied across existing languages

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages by using neutral labels or abbreviated time units
- normalized Swedish passkey wording to `nyckel` in the login flow
- repaired 143 genuine source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established language-specific passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in authentication and administrative operations.
- Brazilian Portuguese uses `chave de acesso`, `senha`, `usuário`, `arquivo`, `backup`, `restauração`, and `servidor` consistently.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## High-risk and contamination correction pass

Fifty-five strings received an additional key-by-key editorial correction pass:

| Locale | Corrected strings | Reviewed areas | Native sign-off |
|---|---:|---|---|
| Japanese (`ja`) | 19 | destructive actions, credentials, subscriptions, compliance, restore, disaster recovery, firewall and migration | Pending |
| Brazilian Portuguese (`pt`) | 21 | subscription consequences, two-factor authentication, compliance, OpenLiteSpeed, migration, deletion prompts, files, providers and cross-language contamination | Pending |
| Simplified Chinese (`zh`) | 15 | destructive actions, credentials, two-factor authentication, subscriptions, compliance, restore, disaster recovery, firewall and OpenLiteSpeed | Pending |

`tests/test_high_risk_locale_overrides.py` requires the exact locale counts and reviewed values, compares every placeholder with the English catalog in the signed source archive, reconstructs the final Portuguese catalog, and rejects known cross-language residue.

## Native reviewer sign-off

For each release-candidate locale, record the reviewer name, review date, covered domains, requested changes, and final decision in the pull-request discussion. Approval must explicitly cover legal, billing, tax, contractual, security, compliance, backup, restore, migration, and jurisdiction-specific customer-facing language.

## Acceptance boundary

The catalogs are structurally complete and receive language-specific machine-assisted translation plus protected-token, terminology, high-risk editorial, and Portuguese contamination passes. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates. Specialized legal, billing, tax, contractual, and jurisdiction-specific compliance wording still requires review by native-speaking subject-matter experts before it is used as binding customer language.
