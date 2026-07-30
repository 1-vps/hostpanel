# Localization review — thirteen-language overlay

## Scope

This overlay retains the ten existing production catalogs and adds Japanese (`ja`), Portuguese (`pt`), and Simplified Chinese (`zh`). All thirteen catalogs contain the same 2,215 stable keys.

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

## Editorial corrections applied across existing languages

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages by using neutral labels or abbreviated time units
- normalized Swedish passkey wording to `nyckel` in the login flow
- repaired 143 genuine source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established language-specific passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in authentication and administrative operations.
- Portuguese uses region-neutral administrative wording, including `chave de acesso`, `backup`, `restauração`, and `servidor`.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## High-risk correction pass

Forty strings received an additional key-by-key editorial correction pass:

| Locale | Corrected strings | Reviewed areas | Native sign-off |
|---|---:|---|---|
| Japanese (`ja`) | 19 | destructive actions, credentials, subscriptions, compliance, restore, disaster recovery, firewall and migration | Pending |
| Portuguese (`pt`) | 6 | subscription consequences, two-factor authentication, compliance, OpenLiteSpeed and migration | Pending |
| Simplified Chinese (`zh`) | 15 | destructive actions, credentials, two-factor authentication, subscriptions, compliance, restore, disaster recovery, firewall and OpenLiteSpeed | Pending |

`tests/test_high_risk_locale_overrides.py` requires the exact locale counts and reviewed values and compares every placeholder with the English catalog in the signed source archive.

## Native reviewer sign-off

For each release-candidate locale, record the reviewer name, review date, covered domains, requested changes, and final decision in the pull-request discussion. Approval must explicitly cover legal, billing, tax, contractual, security, compliance, backup, restore, migration, and jurisdiction-specific customer-facing language.

## Acceptance boundary

The catalogs are structurally complete and receive language-specific machine-assisted translation plus protected-token, terminology, and high-risk editorial passes. Japanese, Portuguese, and Simplified Chinese remain release candidates. Specialized legal, billing, tax, contractual, and jurisdiction-specific compliance wording still requires review by native-speaking subject-matter experts before it is used as binding customer language.
