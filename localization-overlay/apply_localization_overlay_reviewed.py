#!/usr/bin/env python3
"""Apply the core localization overlay plus reviewed language corrections."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
CORE_PATH = ROOT / "apply_localization_overlay.py"
BRAZILIAN_PORTUGUESE_LABEL = "Português (Brasil)"
FINAL_OVERRIDE_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.ja-02.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
)
PORTUGUESE_UI_OVERRIDE_FILES = ("catalog-visible-ui-overrides.pt.json",)
EXPECTED_BASE_COUNTS = {"ja": 19, "pt": 21, "zh": 15}
EXPECTED_BASE_CANONICAL_SHA256 = "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604"
EXPECTED_FINAL_COUNTS = {"ja": 91, "pt": 31, "zh": 60}
EXPECTED_FINAL_CANONICAL_SHA256 = "5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f"
EXPECTED_VISIBLE_COUNTS = {"ja": 110, "pt": 52, "zh": 75}
EXPECTED_VISIBLE_CANONICAL_SHA256 = "6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec"
EXPECTED_UI_COUNTS = {"pt": 80}
EXPECTED_UI_CANONICAL_SHA256 = "193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c"


def canonical_sha256(payload: dict[str, dict[str, str]]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_reviewed_payload(
    label: str,
    payload: dict[str, dict[str, str]],
    expected_counts: dict[str, int],
    expected_digest: str,
) -> None:
    if set(payload) != set(expected_counts):
        raise SystemExit(
            f"{label} locale mismatch: "
            f"expected {sorted(expected_counts)}, got {sorted(payload)}"
        )
    for locale, entries in payload.items():
        if not isinstance(entries, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not value.strip()
            for key, value in entries.items()
        ):
            raise SystemExit(f"{label}: invalid reviewed entries for {locale}")
    counts = {locale: len(entries) for locale, entries in payload.items()}
    if counts != expected_counts:
        raise SystemExit(
            f"{label} count mismatch: expected {expected_counts}, got {counts}"
        )
    digest = canonical_sha256(payload)
    if digest != expected_digest:
        raise SystemExit(
            f"{label} digest mismatch: expected {expected_digest}, got {digest}"
        )


def git_output(repository: pathlib.Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"could not execute Git object verification: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Git returned no error details"
        raise SystemExit(f"Git object verification failed: {detail}")
    return completed.stdout.strip()


def verify_checkout_git_objects(
    overlay: pathlib.Path,
    file_names: tuple[str, ...],
) -> None:
    repository = overlay.parent
    head = git_output(repository, "rev-parse", "--verify", "HEAD^{commit}")
    for file_name in file_names:
        path = overlay / file_name
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"missing or unsafe reviewed Git object: {file_name}")
        try:
            relative = path.relative_to(repository).as_posix()
        except ValueError as exc:
            raise SystemExit(
                f"reviewed file is outside the selected Git checkout: {file_name}"
            ) from exc
        expected = git_output(repository, "rev-parse", f"{head}:{relative}")
        actual = git_output(repository, "hash-object", "--", str(path))
        if actual != expected:
            raise SystemExit(
                f"reviewed file does not match its selected Git object: {file_name}"
            )


def load_core():
    spec = importlib.util.spec_from_file_location(
        "hostpanel_localization_core",
        CORE_PATH,
    )
    if spec is None or spec.loader is None:
        raise SystemExit("could not load core localization overlay")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_language_labels(core) -> None:
    updated: list[tuple[str, str]] = []
    replaced = 0
    for code, label in core.LANGUAGES:
        if code == "pt":
            updated.append((code, BRAZILIAN_PORTUGUESE_LABEL))
            replaced += 1
        else:
            updated.append((code, label))
    if replaced != 1:
        raise SystemExit(
            f"expected exactly one Portuguese language entry, found {replaced}"
        )
    core.LANGUAGES = updated


def load_review_files(
    overlay: pathlib.Path,
    file_names: tuple[str, ...],
    pattern: str,
    allowed_locales: frozenset[str],
    label: str,
) -> dict[str, dict[str, str]]:
    expected = [overlay / name for name in file_names]
    expected_set = set(expected)
    missing = [
        path.name
        for path in expected
        if not path.is_file() or path.is_symlink()
    ]
    discovered = sorted(overlay.glob(pattern))
    unexpected = [path.name for path in discovered if path not in expected_set]
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {label} files: {missing}")
        if unexpected:
            details.append(f"unexpected {label} files: {unexpected}")
        raise SystemExit(f"{label} layout mismatch: " + "; ".join(details))

    result: dict[str, dict[str, str]] = {}
    for path in expected:
        try:
            file_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path.name}: invalid JSON: {exc}") from exc
        if (
            not isinstance(file_payload, dict)
            or not set(file_payload).issubset(allowed_locales)
        ):
            raise SystemExit(f"{path.name}: invalid locales for {label}")
        for locale, entries in file_payload.items():
            if not isinstance(entries, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not value.strip()
                for key, value in entries.items()
            ):
                raise SystemExit(
                    f"{path.name}: invalid {label} entries for {locale}"
                )
            target = result.setdefault(locale, {})
            duplicate = sorted(set(target) & set(entries))
            if duplicate:
                raise SystemExit(
                    f"{path.name}: duplicate {label} keys: {duplicate[:8]}"
                )
            target.update(entries)
    return result


def install_final_override_loader(core) -> None:
    original = core.load_override_bundle

    def load_override_bundle(
        overlay: pathlib.Path,
        overrides: dict[str, dict[str, str]],
    ) -> None:
        base_payload = {
            locale: dict(entries)
            for locale, entries in overrides.items()
        }
        validate_reviewed_payload(
            "base reviewed override",
            base_payload,
            EXPECTED_BASE_COUNTS,
            EXPECTED_BASE_CANONICAL_SHA256,
        )
        verify_checkout_git_objects(
            overlay,
            FINAL_OVERRIDE_FILES + PORTUGUESE_UI_OVERRIDE_FILES,
        )
        original(overlay, overrides)

        final_payload = load_review_files(
            overlay,
            FINAL_OVERRIDE_FILES,
            "catalog-final-overrides.*.json",
            frozenset(core.RELEASE_CANDIDATES),
            "final semantic override",
        )
        validate_reviewed_payload(
            "final semantic override",
            final_payload,
            EXPECTED_FINAL_COUNTS,
            EXPECTED_FINAL_CANONICAL_SHA256,
        )

        visible_payload = {
            locale: dict(entries)
            for locale, entries in base_payload.items()
        }
        for locale, entries in final_payload.items():
            overlap = sorted(set(visible_payload[locale]) & set(entries))
            if overlap:
                raise SystemExit(
                    f"reviewed override layers overlap for {locale}: "
                    f"{overlap[:8]}"
                )
            visible_payload[locale].update(entries)
        validate_reviewed_payload(
            "combined source-visible override",
            visible_payload,
            EXPECTED_VISIBLE_COUNTS,
            EXPECTED_VISIBLE_CANONICAL_SHA256,
        )

        ui_payload = load_review_files(
            overlay,
            PORTUGUESE_UI_OVERRIDE_FILES,
            "catalog-visible-ui-overrides.*.json",
            frozenset({"pt"}),
            "Portuguese UI override",
        )
        validate_reviewed_payload(
            "Portuguese UI override",
            ui_payload,
            EXPECTED_UI_COUNTS,
            EXPECTED_UI_CANONICAL_SHA256,
        )
        ui_overlap = sorted(
            set(visible_payload["pt"]) & set(ui_payload["pt"])
        )
        if ui_overlap:
            raise SystemExit(
                "Portuguese UI override overlaps reviewed values: "
                f"{ui_overlap[:8]}"
            )

        for locale, entries in final_payload.items():
            overrides.setdefault(locale, {}).update(entries)
        for locale, entries in ui_payload.items():
            overrides.setdefault(locale, {}).update(entries)

    core.load_override_bundle = load_override_bundle


def main() -> int:
    core = load_core()
    install_language_labels(core)
    install_final_override_loader(core)
    return core.main()


if __name__ == "__main__":
    sys.exit(main())
