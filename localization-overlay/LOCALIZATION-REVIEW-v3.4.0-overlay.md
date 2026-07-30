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

## Editorial corrections applied across existing languages

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages by using neutral labels or abbreviated time units
- normalized Swedish passkey wording to `nyckel` in the login flow
- repaired genuine source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established language-specific passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in authentication and administrative operations.
- Portuguese uses region-neutral administrative wording, including `chave de acesso`, `backup`, `restauração`, and `servidor`.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## Acceptance boundary

The catalogs are structurally complete and receive language-specific machine-assisted translation plus a protected-token and terminology pass. Specialized legal, billing, tax, contractual, and jurisdiction-specific compliance wording still requires review by native-speaking subject-matter experts before it is used as binding customer language.
