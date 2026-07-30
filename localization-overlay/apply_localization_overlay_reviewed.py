#!/usr/bin/env python3
"""Apply the core localization overlay plus reviewed final-language corrections."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
CORE_PATH = ROOT / "apply_localization_overlay.py"
FINAL_OVERRIDE_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
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

        seen: dict[str, set[str]] = {}
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
                duplicate = sorted(seen.setdefault(locale, set()) & set(entries))
                if duplicate:
                    raise SystemExit(f"{path.name}: duplicate final override keys: {duplicate[:8]}")
                seen[locale].update(entries)
                overrides.setdefault(locale, {}).update(entries)

    core.load_override_bundle = load_override_bundle


def main() -> int:
    core = load_core()
    install_final_override_loader(core)
    return core.main()


if __name__ == "__main__":
    sys.exit(main())
