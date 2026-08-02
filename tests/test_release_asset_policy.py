from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-release.yml"


class ReleaseAssetPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_existing_release_requires_exact_asset_set(self) -> None:
        for name in (
            '"hostpanel-v${VERSION}-update.tar.gz"',
            '"hostpanel-v${VERSION}-update.tar.gz.sig"',
            "hostpanel-update-manifest.json",
            "hostpanel-update-manifest.json.sig",
            "release-build.json",
        ):
            self.assertIn(name, self.workflow)
        self.assertIn("jq -r '.assets[].name'", self.workflow)
        self.assertIn("does not contain exactly the reviewed asset set", self.workflow)

    def test_asset_sizes_are_checked_before_download(self) -> None:
        self.assertIn("maximum=$((100 * 1024 * 1024))", self.workflow)
        self.assertIn("maximum=$((64 * 1024))", self.workflow)
        self.assertIn("maximum=$((1024 * 1024))", self.workflow)
        self.assertIn("unsafe advertised size", self.workflow)
        self.assertLess(
            self.workflow.index("unsafe advertised size"),
            self.workflow.index('gh release download "$TAG"'),
        )

    def test_download_is_pattern_limited(self) -> None:
        self.assertIn('--pattern "hostpanel-v${VERSION}-update.tar.gz"', self.workflow)
        self.assertIn('--pattern "hostpanel-v${VERSION}-update.tar.gz.sig"', self.workflow)
        self.assertIn("--pattern hostpanel-update-manifest.json", self.workflow)
        self.assertIn("--pattern hostpanel-update-manifest.json.sig", self.workflow)
        self.assertIn("--pattern release-build.json", self.workflow)


if __name__ == "__main__":
    unittest.main()
