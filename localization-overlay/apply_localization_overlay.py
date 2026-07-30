#!/usr/bin/env python3
"""Apply the reviewed 13-language HostPanel overlay to an extracted source tree."""
from __future__ import annotations

import argparse
import ast
import base64
import binascii
import csv
import gzip
import hashlib
import json
import pathlib
import pprint
import re
import shutil
import sys

LANGUAGES = [
    ("da", "Dansk"),
    ("de", "Deutsch"),
    ("en", "English"),
    ("es", "Español"),
    ("fi", "Suomi"),
    ("fr", "Français"),
    ("ja", "日本語"),
    ("nb", "Norsk bokmål"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("pt", "Português"),
    ("sv", "Svenska"),
    ("zh", "简体中文"),
]
RELEASE_CANDIDATES = {"ja", "pt", "zh"}
OVERRIDE_ENCODED_LENGTH = 63168
OVERRIDE_ENCODED_SHA256 = "e22307d7ab0d10d97d954ceb8f673eed3c45d57d70a6e16ef61bbc8ec1851336"
OVERRIDE_CHUNK_FILES = (
    "catalog-overrides.json.gz.b64.chunk01",
    "catalog-overrides.json.gz.b64.chunk02",
    "catalog-overrides.json.gz.b64.chunk03",
    "catalog-overrides.json.gz.b64.chunk04a",
    "catalog-overrides.json.gz.b64.chunk04b",
    "catalog-overrides.json.gz.b64.chunk04c",
    "catalog-overrides.json.gz.b64.chunk04d",
    "catalog-overrides.json.gz.b64.chunk05",
    "catalog-overrides.json.gz.b64.chunk06",
    "catalog-overrides.json.gz.b64.chunk07a",
    "catalog-overrides.json.gz.b64.chunk07b",
    "catalog-overrides.json.gz.b64.chunk07c",
    "catalog-overrides.json.gz.b64.chunk07d",
    "catalog-overrides.json.gz.b64.chunk08a",
    "catalog-overrides.json.gz.b64.chunk08b",
    "catalog-overrides.json.gz.b64.chunk08c",
    "catalog-overrides.json.gz.b64.chunk08d",
)
COPY_FIELDS = (
    "title", "subtitle", "username", "password", "code", "code_placeholder",
    "verify", "signin", "passkey", "skip", "language", "continue_with",
)
ERROR_FIELDS = (
    "expired", "locked", "wrong_credentials", "attempts_left",
    "local_admin_disabled", "admin_mfa_required", "invalid_code",
)


def replace_once(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one exact replacement, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def replace_regex(path: pathlib.Path, pattern: str, replacement: str, flags: int = 0) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{path}: expected one regex replacement, found {count}")
    path.write_text(updated, encoding="utf-8")


def replace_python_assignment(path: pathlib.Path, name: str, value: object) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    target = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == name for item in node.targets):
            target = node
            break
    if target is None or target.end_lineno is None:
        raise SystemExit(f"{path}: assignment {name} not found")
    lines = text.splitlines(keepends=True)
    rendered = f"{name} = " + pprint.pformat(value, width=100, sort_dicts=False) + "\n"
    lines[target.lineno - 1 : target.end_lineno] = [rendered]
    path.write_text("".join(lines), encoding="utf-8")


def load_override_bundle(overlay: pathlib.Path, overrides: dict[str, dict[str, str]]) -> None:
    expected = [overlay / name for name in OVERRIDE_CHUNK_FILES]
    missing = [path.name for path in expected if not path.is_file() or path.is_symlink()]
    discovered = sorted(overlay.glob("catalog-overrides.json.gz.b64.*"))
    expected_set = set(expected)
    unexpected = [path.name for path in discovered if path not in expected_set]
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing chunks: {missing}")
        if unexpected:
            details.append(f"unexpected bundle files: {unexpected}")
        raise SystemExit("compressed override bundle layout mismatch: " + "; ".join(details))

    encoded = b"".join(path.read_bytes().strip() for path in expected)
    if len(encoded) != OVERRIDE_ENCODED_LENGTH:
        raise SystemExit(
            f"compressed override bundle length mismatch: {len(encoded)} != {OVERRIDE_ENCODED_LENGTH}"
        )
    encoded_sha256 = hashlib.sha256(encoded).hexdigest()
    if encoded_sha256 != OVERRIDE_ENCODED_SHA256:
        raise SystemExit(
            "compressed override bundle checksum mismatch: "
            f"{encoded_sha256} != {OVERRIDE_ENCODED_SHA256}"
        )
    try:
        packed = base64.b64decode(encoded, validate=True)
        payload = json.loads(gzip.decompress(packed).decode("utf-8"))
    except (binascii.Error, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"compressed override bundle is invalid: {exc}") from exc

    if not isinstance(payload, dict) or set(payload) != RELEASE_CANDIDATES:
        raise SystemExit(
            "compressed override bundle must contain exactly Japanese, Portuguese, and Chinese"
        )
    for locale, entries in payload.items():
        if not isinstance(entries, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value.strip()
            for key, value in entries.items()
        ):
            raise SystemExit(f"compressed override bundle: invalid entries for {locale}")
        target = overrides.setdefault(locale, {})
        duplicate = sorted(set(target) & set(entries))
        if duplicate:
            raise SystemExit(f"compressed override bundle: duplicate override keys: {duplicate[:8]}")
        target.update(entries)


def write_language_status(path: pathlib.Path, key_count: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("language", "status", "keys", "coverage", "fallback"))
        for code, _label in LANGUAGES:
            status = "release-candidate" if code in RELEASE_CANDIDATES else "production"
            writer.writerow((code, status, key_count, "100.0%", 0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=pathlib.Path)
    parser.add_argument("overlay_root", type=pathlib.Path)
    args = parser.parse_args()
    source = args.source_root.resolve()
    overlay = args.overlay_root.resolve()
    if not (source / "app/static/i18n.en.json").is_file():
        raise SystemExit("source root does not contain HostPanel locale catalogs")

    messages = json.loads((overlay / "login-messages.json").read_text(encoding="utf-8"))
    expected_codes = [code for code, _label in LANGUAGES]
    if list(messages) != expected_codes:
        raise SystemExit("login message locale order does not match the language registry")

    catalog_dir = overlay / "catalogs"
    overrides = json.loads((overlay / "catalog-overrides.json").read_text(encoding="utf-8"))
    if not isinstance(overrides, dict):
        raise SystemExit("catalog-overrides.json must contain an object")
    load_override_bundle(overlay, overrides)
    corrections = json.loads((overlay / "existing-catalog-corrections.json").read_text(encoding="utf-8"))
    english_path = source / "app/static/i18n.en.json"
    english = json.loads(english_path.read_text(encoding="utf-8"))
    english_keys = list(english)

    existing_codes = [code for code, _label in LANGUAGES if code not in RELEASE_CANDIDATES]
    for code in existing_codes:
        destination = source / "app/static" / f"i18n.{code}.json"
        catalog = json.loads(destination.read_text(encoding="utf-8"))
        if list(catalog) != english_keys:
            raise SystemExit(f"{destination.name}: source key parity or order mismatch")
        unknown = sorted(set(corrections.get(code, {})) - set(catalog))
        if unknown:
            raise SystemExit(f"{code}: unknown correction keys: {unknown[:8]}")
        catalog.update(corrections.get(code, {}))
        destination.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    for code in ("ja", "pt", "zh"):
        catalog_path = catalog_dir / f"i18n.{code}.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if list(catalog) != english_keys:
            raise SystemExit(f"{catalog_path.name}: key parity or order mismatch")
        unknown_overrides = sorted(set(overrides.get(code, {})) - set(catalog))
        if unknown_overrides:
            raise SystemExit(f"{code}: unknown catalog override keys: {unknown_overrides[:8]}")
        catalog.update(overrides.get(code, {}))
        destination = source / "app/static" / catalog_path.name
        destination.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    language_codes_js = "[" + ",".join(repr(code) for code in expected_codes) + "]"
    panel_i18n = source / "app/static/panel-i18n.js"
    replace_regex(
        panel_i18n,
        r"const HP_LANGUAGES=Object\.freeze\(\[[^\n]+\]\);",
        f"const HP_LANGUAGES=Object.freeze({language_codes_js});",
    )

    login_js = source / "app/static/login.js"
    replace_regex(
        login_js,
        r"const LOGIN_LANGUAGES=Object\.freeze\(\[[^\n]+\]\);",
        f"const LOGIN_LANGUAGES=Object.freeze({language_codes_js});",
    )
    compact_messages = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    replace_regex(
        login_js,
        r"const LOGIN_MESSAGES=\{.*?\};\nfunction formatLoginMessage",
        f"const LOGIN_MESSAGES={compact_messages};\nfunction formatLoginMessage",
        flags=re.S,
    )

    login_copy = {code: {key: messages[code][key] for key in COPY_FIELDS} for code in expected_codes}
    login_errors = {code: {key: messages[code][key] for key in ERROR_FIELDS} for code in expected_codes}
    main_py = source / "app/main.py"
    replace_python_assignment(main_py, "LOGIN_COPY", login_copy)
    replace_python_assignment(main_py, "LOGIN_ERRORS", login_errors)

    login_options = "\n".join(
        f'<option value="{code}"{{% if login_language == \'{code}\' %}} selected{{% endif %}}>{label}</option>'
        for code, label in LANGUAGES
    )
    login_html = source / "app/templates/login.html"
    replace_regex(
        login_html,
        r'(<select aria-label="\{\{ login_copy\.language \}\}" class="lang" id="loginLanguage" required>\n).*?(\n</select>)',
        lambda match: match.group(1) + login_options + match.group(2),
        flags=re.S,
    )

    panel_options = "".join(f'<option value="{code}">{label}</option>' for code, label in LANGUAGES)
    panel_html = source / "app/templates/panel.html"
    replace_regex(
        panel_html,
        r'(<select aria-label="Language" class="language-select" data-i18n-aria-label="ui\.language\.2" id="languageSelect">\n).*?(\n</select>)',
        lambda match: match.group(1) + panel_options + match.group(2),
        flags=re.S,
    )

    production_set = "{" + ", ".join(json.dumps(code) for code in expected_codes) + "}"
    audit = source / "tools/audit_locales.py"
    replace_regex(audit, r"PRODUCTION_LOCALES = \{[^\n]+\}", f"PRODUCTION_LOCALES = {production_set}")

    test_ui = source / "tests/test_ui_experience.py"
    labels = ",".join(repr(label) for _code, label in LANGUAGES)
    replace_regex(test_ui, r"production_languages=\([^\n]+\)", f"production_languages=({labels})")

    tuple_codes = "(" + ", ".join(repr(code) for code in expected_codes) + ")"
    compact_codes = "[" + ",".join(repr(code) for code in expected_codes) + "]"

    test_ux = source / "tests/test_ui_ux_remediation.py"
    replace_regex(test_ux, r"locales=\([^\n]+\)", f"locales={tuple_codes}")
    replace_regex(
        test_ux,
        r'Object\.freeze\(\[[^\n]+\]\)" in js',
        f'Object.freeze({compact_codes})" in js',
    )

    test_v221 = source / "tests/test_v221_audit_fixes.py"
    replace_regex(
        test_v221,
        r"(def test_all_complete_locales_are_selectable\(\) -> None:\n(?:.*\n){2})    locales = \([^\n]+\)",
        lambda match: match.group(1) + f"    locales = {tuple_codes}",
    )
    replace_regex(
        test_v221,
        r'expected = "Object\.freeze\(\[[^\n]+\]\)"',
        f'expected = "Object.freeze({compact_codes})"',
    )
    replace_regex(
        test_v221,
        r"(def test_production_catalogs_have_exact_parity_and_no_english_prose\(\) -> None:\n    from tools\.audit_locales import suspicious_source_identical\n)    locales = \([^\n]+\)",
        lambda match: match.group(1) + f"    locales = {tuple_codes}",
    )

    test_v270 = source / "tests/test_v270_completion.py"
    replace_regex(
        test_v270,
        r'for locale in \("da", "de", "en", "es", "fi", "fr", "nb", "nl", "pl", "sv"\):',
        f"for locale in {tuple_codes}:",
    )

    shutil.copyfile(overlay / "review_locales.py", source / "tools/review_locales.py")
    (source / "tools/review_locales.py").chmod(0o755)

    shutil.copyfile(overlay / "LOCALIZATION.md", source / "LOCALIZATION.md")
    shutil.copyfile(overlay / "LOCALIZATION-REVIEW-v3.4.0-overlay.md", source / "LOCALIZATION-REVIEW-v3.4.0-overlay.md")

    readme = source / "README.md"
    replace_once(
        readme,
        "- Ten complete production locales: Danish, German, English, Spanish, Finnish,\n  French, Norwegian Bokmål, Dutch, Polish, and Swedish.",
        "- Ten production locales: Danish, German, English, Spanish, Finnish, French,\n"
        "  Norwegian Bokmål, Dutch, Polish, and Swedish.\n"
        "- Three release-candidate locales: Japanese, Portuguese, and Simplified Chinese.",
    )
    replace_once(
        readme,
        "| Localization and UI | [`LOCALIZATION.md`](LOCALIZATION.md), [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) |",
        "| Localization and UI | [`LOCALIZATION.md`](LOCALIZATION.md), [`LOCALIZATION-REVIEW-v3.4.0-overlay.md`](LOCALIZATION-REVIEW-v3.4.0-overlay.md), [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) |",
    )

    write_language_status(source / "releases/language-status-v3.4.0.csv", len(english))
    capability_status = source / "releases/capability-status-v3.4.0.csv"
    replace_once(
        capability_status,
        'localization-expansion,P2,localization,complete-current-catalogs,"10 production languages; 2,208 keys; locale and runtime gates",additional languages still need full translation and release review',
        f'localization-expansion,P2,localization,complete-current-catalogs,"10 production languages and 3 release-candidate languages; {len(english):,} keys; locale and runtime gates",native-speaker review remains required for specialized legal billing and compliance wording',
    )
    print(f"Applied {len(expected_codes)} supported locales with {len(english)} keys each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
