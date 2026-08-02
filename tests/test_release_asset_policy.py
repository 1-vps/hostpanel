from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-release.yml"


class ReleaseAssetPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_existing_release_requires_exact_eighteen_asset_set(self) -> None:
        payload_block = self.workflow.index("            payloads=(")
        payload_end = self.workflow.index("            )", payload_block)
        payloads = self.workflow[payload_block:payload_end]
        for name in (
            '"hostpanel-v${VERSION}-update.tar.gz"',
            "hostpanel-update-manifest.json",
            "release-build.json",
            '"hostpanel-v${RELEASE_ID}-source.tar.gz"',
            "source-build.json",
            "sbom.cdx.json",
            "dependency-lock-attestation.json",
            "source-file-manifest.json",
            "SOURCE-CANDIDATE-SHA256SUMS",
        ):
            self.assertIn(name, payloads)
        self.assertIn('expected_names+=("$payload" "$payload.sig")', self.workflow)
        self.assertIn("jq -r '.assets[].name'", self.workflow)
        self.assertIn("does not contain exactly the reviewed asset set", self.workflow)
        self.assertIn('[[ "${#actual_assets[@]}" -eq 18 ]]', self.workflow)
        self.assertIn('[[ "${#assets[@]}" -eq 18 ]]', self.workflow)

    def test_asset_sizes_are_checked_before_download(self) -> None:
        self.assertIn("maximum=$((2 * 1024 * 1024 * 1024))", self.workflow)
        self.assertIn("maximum=$((100 * 1024 * 1024))", self.workflow)
        self.assertIn("maximum=$((64 * 1024))", self.workflow)
        self.assertIn("maximum=$((1024 * 1024))", self.workflow)
        self.assertIn("maximum=$((32 * 1024 * 1024))", self.workflow)
        self.assertIn("unsafe advertised size", self.workflow)
        self.assertLess(
            self.workflow.index("unsafe advertised size"),
            self.workflow.index('gh release download "$TAG"'),
        )

    def test_download_is_preceded_and_followed_by_exact_set_checks(self) -> None:
        download = self.workflow.index('gh release download "$TAG"')
        api_exact_set = self.workflow.index(
            "does not contain exactly the reviewed asset set"
        )
        pre_download_signature_loop = self.workflow.index(
            'for payload in "${payloads[@]}"; do',
            download,
        )
        self.assertLess(api_exact_set, download)
        self.assertGreater(pre_download_signature_loop, download)
        self.assertIn(
            '[[ "$(printf \'%s\\n\' "${downloaded_assets[@]}")" == "$(printf \'%s\\n\' "${local_assets[@]}")" ]]',
            self.workflow,
        )
        self.assertIn('cmp -- "dist/$asset" "$release_dir/$asset"', self.workflow)

    def test_every_payload_including_provenance_is_signed_and_verified(self) -> None:
        self.assertIn("              release-build.json", self.workflow)
        sign_loop = self.workflow.index('for payload in "${payloads[@]}"; do')
        sign_command = self.workflow.index(
            "openssl pkeyutl -sign -inkey \"$key_file\" -rawin",
            sign_loop,
        )
        signature_output = self.workflow.index(
            '-out "dist/$payload.sig"',
            sign_command,
        )
        verification = self.workflow.index(
            "openssl pkeyutl -verify -pubin -inkey releases/update.pub -rawin",
            signature_output,
        )
        self.assertLess(sign_loop, sign_command)
        self.assertLess(sign_command, signature_output)
        self.assertLess(signature_output, verification)
        self.assertIn('expected_assets+=("$payload" "$payload.sig")', self.workflow)
        self.assertIn(
            '-in "$release_dir/$payload" -sigfile "$release_dir/$payload.sig"',
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
