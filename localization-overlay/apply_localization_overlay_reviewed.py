#!/usr/bin/env python3
"""Apply the core localization overlay plus reviewed final-language corrections."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
CORE_PATH = ROOT / "apply_localization_overlay.py"
FINAL_OVERRIDE_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.ja-02.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
)
EXPECTED_BASE_COUNTS = {"ja": 19, "pt": 21, "zh": 15}
EXPECTED_BASE_CANONICAL_SHA256 = (
    "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604"
)
EXPECTED_FINAL_COUNTS = {"ja": 91, "pt": 31, "zh": 60}
EXPECTED_FINAL_CANONICAL_SHA256 = (
    "5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f"
)
EXPECTED_VISIBLE_COUNTS = {"ja": 110, "pt": 52, "zh": 75}
EXPECTED_VISIBLE_CANONICAL_SHA256 = (
    "6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec"
)


def canonical_sha256(payload: dict[str, dict[str, str]]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_reviewed_payload(
    label: str,
    payload: dict[str, dict[str, str]],
    expected_counts: dict[str, int],
    expected_digest: str,
) -> None:
    if set(payload) != set(expected_counts):
        raise SystemExit(
            f"{label} locale mismatch: expected {sorted(expected_counts)}, got {sorted(payload)}"
        )
    for locale, entries in payload.items():
        if not isinstance(entries, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value.strip()
            for key, value in entries.items()
        ):
            raise SystemExit(f"{label}: invalid reviewed entries for {locale}")
    counts = {locale: len(entries) for locale, entries in payload.items()}
    if counts != expected_counts:
        raise SystemExit(f"{label} count mismatch: expected {expected_counts}, got {counts}")
    digest = canonical_sha256(payload)
    if digest != expected_digest:
        raise SystemExit(
            f"{label} digest mismatch: expected {expected_digest}, got {digest}"
        )


def load_core():
    spec = importlib.util.spec_from_file_location("hostpanel_localization_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load core localization overlay")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_final_override_loader(core) -> None:
    original = core.load_override_bundle

    def load_override_bundle(overlay: pathlib.Path, overrides: dict[str, dict[str, str]]) -> None:
        base_payload = {locale: dict(entries) for locale, entries in overrides.items()}
        validate_reviewed_payload(
            "base reviewed override",
            base_payload,
            EXPECTED_BASE_COUNTS,
            EXPECTED_BASE_CANONICAL_SHA256,
        )

        original(overlay, overrides)
        expected = [overlay / name for name in FINAL_OVERRIDE_FILES]
        missing = [path.name for path in expected if not path.is_file() or path.is_symlink()]
        discovered = sorted(overlay.glob("catalog-final-overrides.*.json"))
        unexpected = [path.name for path in discovered if path not in set(expected)]
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing final overrides: {missing}")
            if unexpected:
                details.append(f"unexpected final overrides: {unexpected}")
            raise SystemExit("final override layout mismatch: " + "; ".join(details))

        final_payload: dict[str, dict[str, str]] = {}
        for path in expected:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name}: invalid JSON: {exc}") from exc
            if not isinstance(payload, dict) or not set(payload).issubset(core.RELEASE_CANDIDATES):
                raise SystemExit(f"{path.name}: final overrides must target release-candidate locales")
            for locale, entries in payload.items():
                if not isinstance(entries, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str) or not value.strip()
                    for key, value in entries.items()
                ):
                    raise SystemExit(f"{path.name}: invalid final overrides for {locale}")
                target = final_payload.setdefault(locale, {})
                duplicate = sorted(set(target) & set(entries))
                if duplicate:
                    raise SystemExit(f"{path.name}: duplicate final override keys: {duplicate[:8]}")
                target.update(entries)

        validate_reviewed_payload(
            "final semantic override",
            final_payload,
            EXPECTED_FINAL_COUNTS,
            EXPECTED_FINAL_CANONICAL_SHA256,
        )

        visible_payload = {locale: dict(entries) for locale, entries in base_payload.items()}
        for locale, entries in final_payload.items():
            overlap = sorted(set(visible_payload[locale]) & set(entries))
            if overlap:
                raise SystemExit(
                    f"reviewed override layers overlap for {locale}: {overlap[:8]}"
                )
            visible_payload[locale].update(entries)
        validate_reviewed_payload(
            "combined source-visible override",
            visible_payload,
            EXPECTED_VISIBLE_COUNTS,
            EXPECTED_VISIBLE_CANONICAL_SHA256,
        )

        for locale, entries in final_payload.items():
            overrides.setdefault(locale, {}).update(entries)

    core.load_override_bundle = load_override_bundle


def main() -> int:
    core = load_core()
    install_final_override_loader(core)
    return core.main()


if __name__ == "__main__":
    sys.exit(main())
