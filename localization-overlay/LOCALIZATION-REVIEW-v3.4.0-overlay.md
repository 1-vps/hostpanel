# Localization review — thirteen-language overlay

## Scope

This overlay retains the ten existing production catalogs and adds Japanese (`ja`), Portuguese (`pt`), and Simplified Chinese (`zh`) as release candidates. All thirteen catalogs contain the same 2,215 stable keys. The single `pt` catalog follows Brazilian Portuguese terminology, is displayed as `Português (Brasil)`, and receives generic and regional Portuguese tags; a separate European Portuguese catalog is not included.

PR #11 separately supplies ten redesign-owned dynamic dashboard strings for each release-candidate locale. Those 30 strings are part of the native-review surface but not part of the 317 catalog-correction count below.

## Automated checks

- exact key parity and order against English
- non-empty UTF-8 values and placeholder parity
- unsafe-markup rejection and protected-token validation
- source-identical English prose detection with technical allowlists
- panel and login selector coverage
- regional locale normalization
- synchronized server- and client-rendered login messages
- exact canonical digests for explicit release-candidate review layers
- reconstruction of final override state after the checksum-locked bundle
- exact file layout and Git blobs for production-catalog corrections
- contamination checks scoped to explicitly reviewed Brazilian Portuguese values
- product-name preservation for `cPanel` and `DirectAdmin`
- exact Git-object verification for reviewed runtime JSON files
- future redesign-copy key and placeholder parity
- Ed25519 source-archive verification before localization extraction
- safe extraction limits for paths, links, devices, member types, member count, expanded size, and top-level root
- stacked-PR workflow coverage without a `main`-only pull-request filter

## Existing production-language corrections

- removed parenthetical singular/plural constructions from login lockout and remaining-attempt messages
- normalized Swedish passkey wording to `nyckel`
- repaired 143 source-identical Swedish interface labels while preserving examples, paths, commands, certificates, protocols, brands, and identifiers
- retained established passkey terminology in Danish, German, Spanish, Finnish, French, Norwegian Bokmål, Dutch, and Polish

Locked correction blobs:

```text
existing-catalog-corrections.json              b0b6a0b6f2176ba5f6c98d3ac6d80dcca5e7c43f
existing-catalog-corrections.sv-ui-1.json      9011dfafc66d79e61e7384b44e8900d64605edc5
existing-catalog-corrections.sv-ui-2.json      a7b45ab2320169c8ad4cffd10956756cc637b2b6
```

## New-language terminology

- Japanese uses `パスキー`, `サーバー`, `復元`, and `バックアップ` consistently in reviewed authentication and administrative text.
- Brazilian Portuguese uses `chave de acesso`, `senha`, `usuário`, `arquivo`, `backup`, `restauração`, and `servidor` in reviewed values.
- Simplified Chinese uses `通行密钥`, `服务器`, `备份`, `恢复`, and `防火墙` consistently.

## Reviewed catalog-correction layers

The locked compressed translation bundle is preserved byte-for-byte. Additional reviewed corrections are explicit and non-overlapping:

| Locale | Initial high-risk / contamination | Semantic final | Visible UI | Total explicit catalog corrections | Native sign-off |
|---|---:|---:|---:|---:|---|
| Japanese (`ja`) | 19 | 91 | 0 | 110 | Pending |
| Brazilian Portuguese (`pt`) | 21 | 31 | 80 | 132 | Pending |
| Simplified Chinese (`zh`) | 15 | 60 | 0 | 75 | Pending |
| **Total** | **55** | **182** | **80** | **317** | **Pending** |

The initial layer covers destructive actions, authentication, credentials, subscriptions, compliance, restore, disaster recovery, firewall, migration, and selected Portuguese contamination. The semantic final layer expands review to account state, passwords, deletion consequences, database and mail operations, backup and staging, migration and rollback, DNS, firewall, control-plane high availability, dialogs, route labels, status messages, and customer-facing explanatory copy. The Portuguese visible-UI layer corrects 80 dialog, validation, and primary-navigation values.

The repository requires:

- initial counts `19/21/15` and SHA-256 `98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604`
- semantic final counts `91/31/60` and SHA-256 `5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f`
- combined initial and semantic counts `110/52/75` and SHA-256 `6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec`
- Portuguese visible-UI count `80` and SHA-256 `193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c`
- every explicitly reviewed key to exist in signed English and preserve placeholders
- reviewed values to win after the locked bundle
- reviewed layers to remain non-overlapping
- tracked Portuguese contamination markers to be absent from the reviewed subset
- all five semantic final files and the Portuguese visible-UI file to match the selected checkout's Git objects before JSON is read
- exactly one `pt` entry displayed as `Português (Brasil)`

## Redesign-owned dynamic strings

Native review must also cover these ten PR #11 keys for `ja`, Brazilian `pt`, and `zh`:

- `refreshDashboardData`
- `waitingForServices`
- `runningCount`
- `runningOf`
- `waitingForQueueStatus`
- `queueRequiresAttention`
- `deliveryQueueHealthy`
- `open`
- `dashboardOverview`
- `live`

Automated tests enforce key and placeholder parity and representative native text, but operational-status semantics still require native review.

## Native reviewer sign-off

Native-speaking subject-matter review is tracked in issue #14. For each release-candidate locale, record reviewer identity, review date, covered domains, requested changes, and final decision. Approval must explicitly cover legal, billing, tax, contractual, security, compliance, backup, restore, migration, destructive actions, jurisdiction-specific customer text, and dynamic dashboard status.

## Acceptance boundary

The catalogs are structurally complete and receive machine-assisted translation plus protected-token, terminology, contamination, high-risk, semantic, visible-UI, and workflow-integrity checks. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates. The 317 catalog corrections and 30 redesign strings are editorial/automated review results, not native-language approval. Unreviewed machine-assisted text—especially the Portuguese base catalog—may still contain awkward wording or cross-language residue.

Full native-speaking subject-matter review is required before contractual or production-critical use. Executed GitHub Actions validation remains separately blocked by issue #13.
