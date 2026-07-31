# HostPanel localization

HostPanel exposes thirteen supported interface-language catalogs in both the panel and the login screen:

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
- Portuguese (`pt`, Brazilian terminology)
- Swedish (`sv`)
- Simplified Chinese (`zh`)

Every supported catalog contains the complete set of 2,215 stable keys. Regional browser tags are normalized to their base locale, so `ja-JP`, `pt-BR`, `pt-PT`, `zh-CN`, and `zh-Hans` resolve to the supported catalog. The single `pt` catalog currently follows Brazilian Portuguese terminology; a separate European Portuguese catalog is not included in this overlay.

The locked compressed bundle establishes complete key coverage for the three new locales. Explicit reviewed layers then provide 317 corrections: 110 Japanese, 132 Brazilian Portuguese, and 75 Simplified Chinese. `apply_localization_overlay_reviewed.py` loads the locked bundle, applies five exact semantic final-override files, and then applies an embedded 80-value Portuguese dialog, validation, and primary-navigation layer.

The release audit rejects missing or unknown keys, blank values, placeholder mismatches, unsafe markup, and suspicious source-identical English prose. The editorial audit additionally rejects repeated CJK corruption, known Japanese machine-translation errors, tracked Portuguese contamination markers, untranslated natural-language labels in Japanese and Simplified Chinese, and regression of the reviewed Swedish UI translations. Repository regressions lock every explicit reviewed layer, verify reviewed placeholders against the signed English source, confirm reviewed values win after the compressed bundle, and preserve product names such as `cPanel` and `DirectAdmin`.

Run:

```bash
python3 tools/audit_locales.py
python3 tools/review_locales.py
python3 -m unittest discover -s tests -p 'test_high_risk_locale_overrides.py' -v
python3 -m unittest discover -s tests -p 'test_portuguese_ui_overrides.py' -v
```

## Editorial policy

Machine assistance may be used to establish complete key parity, but a catalog is not promoted solely because it contains every key. Login, authentication, destructive actions, billing, security, compliance, backup, restore, migration, rollback, DNS, mail, database, staging, and provider operations require additional semantic review. Japanese, Brazilian Portuguese, and Simplified Chinese remain release candidates until native-speaking subject-matter review has covered legal, billing, tax, contractual, security, and jurisdiction-specific language. The 317 explicit corrections are editorial/automated review results, not native-language approval. Unreviewed portions of the machine-assisted catalogs—particularly Portuguese—may still contain awkward wording or cross-language residue. See `LOCALIZATION-REVIEW-v3.4.0-overlay.md` for the review record and acceptance boundary.

## Adding or changing text

1. Add or update the English value in `app/static/i18n.en.json`.
2. Add the same stable key to every other `i18n.<locale>.json` catalog.
3. Preserve placeholders such as `{count}`, commands, URLs, and code examples.
4. Use `t()` or `tr()` for dynamically generated browser text.
5. Update server-rendered and client-rendered login messages together.
6. Put reviewed release-candidate corrections in the appropriate explicit review layer; do not rewrite the locked bundle casually.
7. Run both localization audits, both override regressions, and the complete UI/backend test suite.

Files are UTF-8 JSON. Translate values only; catalog keys are stable API-like identifiers and must not be renamed.
