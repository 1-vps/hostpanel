# Localization review — thirteen-language overlay

## Scope

This overlay retains the ten existing production catalogs and adds Japanese (`ja`), Portuguese (`pt`), and Simplified Chinese (`zh`). All thirteen catalogs contain the same 2,215 stable keys. The single `pt` catalog follows Brazilian Portuguese administrative terminology; generic and regional Portuguese tags currently resolve to it.

## Automated checks

- exact key parity and key order against English
- non-empty UTF-8 string values and placeholder parity
- unsafe-markup rejection and protected-token validation
- source-identical English prose detection with technical allowlists
- panel and login selector coverage
- regional locale normalization
- synchronized server- and client-rendered login messages
- exact canonical digests for both source-visible review layers
- reconstruction of the final override state after the checksum-locked bundle
- reconstruction of the final Brazilian Portuguese catalog and rejection of known Spanish, Italian, French, and European Portuguese residue

## Editorial corrections applied across existing languages

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages by using neutral labels or abbreviated time units
- normalized Swedish passkey wording to `nyckel` in the login flow
- repaired 143 genuine source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established language-specific passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in authentication and administrative operations.
- Brazilian Portuguese uses `chave de acesso`, `senha`, `usuário`, `arquivo`, `backup`, `restauração`, and `servidor` consistently.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## Reviewed correction layers

The locked compressed translation bundle is preserved byte-for-byte. Additional reviewed corrections are source-visible and non-overlapping with each other:

| Locale | Initial high-risk / contamination | Semantic final | Total explicit reviewed values | Native sign-off |
|---|---:|---:|---:|---|
| Japanese (`ja`) | 19 | 37 | 56 | Pending |
| Brazilian Portuguese (`pt`) | 21 | 31 | 52 | Pending |
| Simplified Chinese (`zh`) | 15 | 60 | 75 | Pending |
| **Total** | **55** | **128** | **183** | **Pending** |

The initial layer covers destructive actions, authentication, credentials, subscriptions, compliance, restore, disaster recovery, firewall, migration, and Portuguese language contamination. The semantic final layer expands the pass to account state, passwords, deletion consequences, database and mail operations, backup and staging, migration and rollback, DNS, firewall, control-plane high availability, and customer-facing explanatory copy.

`tests/test_high_risk_locale_overrides.py` requires:

- initial counts `19/21/15` and their canonical digest
- semantic final counts `37/31/60` and SHA-256 `bb44356c5ece1b3b767ffe0cd45cdf657c8ce357ed6ad9ef60785b307ac35250`
- combined source-visible counts `56/52/75` and SHA-256 `f74f4268c699fb6359d261bc3cd77869d055b96ab85f01ba54ce2943842867d0`
- every reviewed key to exist in the signed English source and preserve placeholders
- every semantic final value to win after the locked bundle is loaded
- the final Brazilian Portuguese catalog to contain no known cross-language residue

## Native reviewer sign-off

For each release-candidate locale, record the reviewer name, review date, covered domains, requested changes, and final decision in the pull-request discussion. Approval must explicitly cover legal, billing, tax, contractual, security, compliance, backup, restore, migration, destructive actions, and jurisdiction-specific customer-facing language.

## Acceptance boundary

The catalogs are structurally complete and receive language-specific machine-assisted translation plus protected-token, terminology, contamination, high-risk, and semantic editorial passes. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates. The 183 explicit corrections are editorial/automated review results, not native-language approval. Specialized legal, billing, tax, contractual, security, and jurisdiction-specific compliance wording still requires review by native-speaking subject-matter experts before it is used as binding customer language.
