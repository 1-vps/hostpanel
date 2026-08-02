from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SingleUpdateContractTests(unittest.TestCase):
    def test_legacy_update_artifacts_are_absent(self) -> None:
        forbidden_files = (
            "latest" + ".json",
            "latest" + ".json.sig",
            "PUBLISHING" + "-NOTE.txt",
            "build" + "-console.txt",
            "build" + "-console.json",
            "BUILD" + "-RESULTS.json",
        )
        for name in forbidden_files:
            self.assertFalse((ROOT / name).exists(), name)

    def test_supported_text_paths_do_not_reference_the_legacy_channel(self) -> None:
        forbidden_markers = (
            "downloads." + "example.invalid",
            "tools/" + "build_release.py",
        )
        binary_suffixes = {
            ".gz",
            ".sig",
            ".tar",
            ".zip",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".ico",
            ".woff",
            ".woff2",
        }
        violations: list[str] = []
        for path in sorted(ROOT.rglob("*")):
            if path == pathlib.Path(__file__).resolve() or not path.is_file():
                continue
            if ".git" in path.parts or path.suffix.lower() in binary_suffixes:
                continue
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeError, OSError):
                continue
            for marker in forbidden_markers:
                if marker in text:
                    violations.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(violations, [])

    def test_github_release_manifest_is_the_only_supported_update_contract(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "publish-release.yml").read_text(
            encoding="utf-8"
        )
        updater = (ROOT / "tools" / "hostpanel-update.py").read_text(encoding="utf-8")
        builder = (ROOT / "tools" / "build_update_release_impl.py").read_text(
            encoding="utf-8"
        )
        updates = (ROOT / "UPDATES.md").read_text(encoding="utf-8")
        self.assertIn("hostpanel-update-manifest.json", workflow)
        self.assertIn('gh release create "$TAG"', workflow)
        self.assertIn("api.github.com", updater)
        self.assertIn("hostpanel-update-manifest.json", updater)
        self.assertIn("canonical_manifest", builder)
        self.assertIn("Signed GitHub releases", updates)


if __name__ == "__main__":
    unittest.main()
