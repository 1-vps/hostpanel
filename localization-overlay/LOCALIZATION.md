# HostPanel localization

HostPanel exposes thirteen production-ready interface languages in both the panel and the login screen:

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
- Portuguese (`pt`)
- Swedish (`sv`)
- Simplified Chinese (`zh`)

Every production catalog contains the complete set of 2,215 stable keys. Regional browser tags are normalized to their base locale, so `ja-JP`, `pt-BR`, `pt-PT`, `zh-CN`, and `zh-Hans` resolve to the supported catalog.

The release audit rejects missing or unknown keys, blank values, placeholder mismatches, unsafe markup, and suspicious source-identical English prose. Commands, URLs, paths, code identifiers, certificates, cryptographic terms, and product names remain unchanged where translation would make them invalid or misleading.

Run:

```bash
python3 tools/audit_locales.py
```

## Editorial policy

Machine assistance may be used to establish complete key parity, but a catalog is not promoted solely because it contains every key. Login, authentication, destructive actions, billing, security, compliance, backup, restore, migration, and provider operations require an additional terminology pass. See `LOCALIZATION-REVIEW-v3.4.0-overlay.md` for this expansion's review record and remaining native-speaker acceptance boundary.

## Adding or changing text

1. Add or update the English value in `app/static/i18n.en.json`.
2. Add the same stable key to every other `i18n.<locale>.json` catalog.
3. Preserve placeholders such as `{count}`, commands, URLs, and code examples.
4. Use `t()` or `tr()` for dynamically generated browser text.
5. Update server-rendered and client-rendered login messages together.
6. Run the localization audit and the complete UI/backend test suite.

Files are UTF-8 JSON. Translate values only; catalog keys are stable API-like identifiers and must not be renamed.
