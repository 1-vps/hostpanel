# HostPanel localization

HostPanel exposes thirteen interface-language catalogs in both the panel and login screen:

- Danish (`da`)
- German (`de`)
- English (`en`)
- Spanish (`es`)
- Finnish (`fi`)
- French (`fr`)
- Japanese (`ja`)
- Norwegian Bokmål (`nb`)
- Dutch (`nl`)
- Polish (`pl`)
- Portuguese (`pt`, Brazilian terminology; displayed as **Português (Brasil)**)
- Swedish (`sv`)
- Simplified Chinese (`zh`)

Every catalog contains the complete set of 2,215 stable keys. Regional browser tags are normalized to their base locale, so `ja-JP`, `pt-BR`, `pt-PT`, `zh-CN`, and `zh-Hans` resolve to the supported catalog. The single `pt` catalog follows Brazilian Portuguese terminology; a separate European Portuguese catalog is not included.

The ten original locales remain production locales. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates.

The locked compressed bundle establishes complete key coverage for the three new locales. Explicit reviewed layers provide 317 catalog corrections: 110 Japanese, 132 Brazilian Portuguese, and 75 Simplified Chinese. PR #11 additionally supplies ten redesign-owned dynamic dashboard strings for each new locale. Those strings are placeholder-tested but still require native review.

The release audit rejects missing or unknown keys, blank values, placeholder mismatches, unsafe markup, protected-token drift, and suspicious source-identical English prose. The editorial audit additionally rejects repeated CJK corruption, known Japanese mistranslations, tracked Portuguese contamination, untranslated natural-language labels in Japanese and Simplified Chinese, and regression of reviewed Swedish UI translations.

Repository regressions lock:

- every explicit release-candidate review layer and digest
- the exact production-correction file layout and Git blobs
- placeholders against the signed English source
- final override precedence after the compressed bundle
- product names such as `cPanel` and `DirectAdmin`
- the `Português (Brasil)` selector label
- future `ja`, `pt`, and `zh` redesign copy
- signed-archive verification and safe extraction in the stacked localization workflow

Run:

```bash
python3 tools/audit_locales.py
python3 tools/review_locales.py
python3 -m unittest discover -s tests -p 'test_existing_catalog_corrections.py' -v
python3 -m unittest discover -s tests -p 'test_high_risk_locale_overrides.py' -v
python3 -m unittest discover -s tests -p 'test_portuguese_ui_overrides.py' -v
python3 -m unittest discover -s tests -p 'test_localization_bootstrap_wiring.py' -v
python3 -m unittest discover -s tests -p 'test_localization_workflow_security.py' -v
python3 -m unittest discover -s tests -p 'test_panel_future_locales.py' -v
```

## Editorial policy

Machine assistance may establish complete key parity, but a catalog is not promoted solely because it contains every key. Login, authentication, destructive actions, billing, security, compliance, backup, restore, migration, rollback, DNS, mail, database, staging, provider operations, and dynamic dashboard status require semantic review.

Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates until native-speaking subject-matter review covers legal, billing, tax, contractual, security, jurisdiction-specific, destructive-action, recovery, and operational-status language. The 317 catalog corrections and 30 redesign strings are editorial/automated review results, not native-language approval. Unreviewed machine-assisted text—particularly Portuguese—may still contain awkward wording or cross-language residue.

See `LOCALIZATION-REVIEW-v3.4.0-overlay.md` for the review record and acceptance boundary.

## Adding or changing text

1. Add or update the English value in `app/static/i18n.en.json`.
2. Add the same stable key to every other catalog.
3. Preserve placeholders, commands, URLs, paths, identifiers, and code examples.
4. Use `t()` or `tr()` for dynamically generated browser text.
5. Update server-rendered and client-rendered login messages together.
6. Put reviewed release-candidate corrections in the appropriate explicit review layer; do not rewrite the locked bundle casually.
7. Update the locked production-correction blob contract when an existing-locale correction changes intentionally.
8. Run both audits, all localization/wiring regressions, and the complete UI/backend test suite.

Files are UTF-8 JSON. Translate values only; catalog keys are stable API-like identifiers and must not be renamed.
