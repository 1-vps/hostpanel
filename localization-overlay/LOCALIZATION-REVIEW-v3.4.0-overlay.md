# Localization review — thirteen-language overlay

## Scope

This overlay retains the ten existing production catalogs and adds Japanese (`ja`), Portuguese (`pt`), and Simplified Chinese (`zh`). All thirteen catalogs contain the same 2,215 stable keys. The single `pt` catalog follows Brazilian Portuguese terminology; generic and regional Portuguese tags currently resolve to it.

## Automated checks

- exact key parity and key order against English
- non-empty UTF-8 string values and placeholder parity
- unsafe-markup rejection and protected-token validation
- source-identical English prose detection with technical allowlists
- panel and login selector coverage
- regional locale normalization
- synchronized server- and client-rendered login messages
- exact canonical digests for the explicit reviewed layers
- reconstruction of the final override state after the checksum-locked bundle
- contamination-marker checks scoped to the explicitly reviewed Brazilian Portuguese values
- product-name preservation for `cPanel` and `DirectAdmin`

## Editorial corrections applied across existing languages

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages by using neutral labels or abbreviated time units
- normalized Swedish passkey wording to `nyckel` in the login flow
- repaired 143 genuine source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established language-specific passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in authentication and administrative operations.
- Brazilian Portuguese uses `chave de acesso`, `senha`, `usuário`, `arquivo`, `backup`, `restauração`, and `servidor` in the reviewed values.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## Reviewed correction layers

The locked compressed translation bundle is preserved byte-for-byte. Additional reviewed corrections are explicit and non-overlapping:

| Locale | Initial high-risk / contamination | Semantic final | Visible UI | Total explicit reviewed values | Native sign-off |
|---|---:|---:|---:|---:|---|
| Japanese (`ja`) | 19 | 91 | 0 | 110 | Pending |
| Brazilian Portuguese (`pt`) | 21 | 31 | 80 | 132 | Pending |
| Simplified Chinese (`zh`) | 15 | 60 | 0 | 75 | Pending |
| **Total** | **55** | **182** | **80** | **317** | **Pending** |

The initial layer covers destructive actions, authentication, credentials, subscriptions, compliance, restore, disaster recovery, firewall, migration, and selected Portuguese contamination. The semantic final layer expands the pass to account state, passwords, deletion consequences, database and mail operations, backup and staging, migration and rollback, DNS, firewall, control-plane high availability, dialogs, route labels, status messages, and customer-facing explanatory copy. The embedded Portuguese visible-UI layer corrects 80 dialog, validation, and primary-navigation values.

The repository regressions require:

- initial counts `19/21/15` and SHA-256 `98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604`
- semantic final counts `91/31/60` and SHA-256 `5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f`
- combined initial and semantic counts `110/52/75` and SHA-256 `6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec`
- Portuguese visible-UI count `80` and SHA-256 `193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c`
- every explicitly reviewed key to exist in the signed English source and preserve placeholders
- every reviewed value to win after the locked bundle is loaded
- reviewed layers to remain non-overlapping
- tracked contamination markers to be absent from the explicitly reviewed Portuguese subset

## Native reviewer sign-off

For each release-candidate locale, record the reviewer name, review date, covered domains, requested changes, and final decision in the pull-request discussion. Approval must explicitly cover legal, billing, tax, contractual, security, compliance, backup, restore, migration, destructive actions, and jurisdiction-specific customer-facing language.

## Acceptance boundary

The catalogs are structurally complete and receive language-specific machine-assisted translation plus protected-token, terminology, contamination, high-risk, semantic, and visible-UI editorial passes. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates. The 317 explicit corrections are editorial/automated review results, not native-language approval. The unreviewed portions of the machine-assisted catalogs—especially the Portuguese base catalog—may still contain awkward wording or cross-language residue. Full native-speaking subject-matter review is therefore required before contractual or production-critical use.
