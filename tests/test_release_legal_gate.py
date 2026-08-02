from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "tools" / "release_legal.py"
WRAPPER_PATH = ROOT / "tools" / "build-update-release.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("hostpanel_release_legal", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release legal validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseLegalGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.wrapper = WRAPPER_PATH.read_text(encoding="utf-8")

    def fixture(self, readme: str, license_text: str):
        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        (root / "README.md").write_text(readme, encoding="utf-8")
        (root / "LICENSE").write_text(license_text, encoding="utf-8")
        return temporary, root

    def test_complete_proprietary_metadata_passes(self) -> None:
        temporary, root = self.fixture(
            "# HostPanel\n\n**License:** Proprietary — see [LICENSE](LICENSE).\n",
            "HostPanel End User License Agreement\n\nVersion 1.0 — Effective 2 August 2026\n",
        )
        with temporary:
            self.validator.validate_repository(root)

    def test_unresolved_eula_placeholders_fail_closed(self) -> None:
        temporary, root = self.fixture(
            "# HostPanel\n\n**License:** Proprietary — see [LICENSE](LICENSE).\n",
            "HostPanel End User License Agreement\nEffective [DATE]\nBy [LEGAL ENTITY NAME]\n",
        )
        with temporary, self.assertRaisesRegex(SystemExit, r"\[DATE\].*\[LEGAL ENTITY NAME\]"):
            self.validator.validate_repository(root)

    def test_linked_legal_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            (root / "README.md").write_text(
                "# HostPanel\n\n**License:** Proprietary — see [LICENSE](LICENSE).\n",
                encoding="utf-8",
            )
            target = root / "real-license"
            target.write_text(
                "HostPanel End User License Agreement\nEffective 2 August 2026\n",
                encoding="utf-8",
            )
            (root / "LICENSE").symlink_to(target.name)
            with self.assertRaisesRegex(SystemExit, "opened safely"):
                self.validator.validate_repository(root)

    def test_obsolete_mit_claim_is_rejected(self) -> None:
        temporary, root = self.fixture(
            "# HostPanel\n\n**License:** MIT\n",
            "HostPanel End User License Agreement\nEffective 2 August 2026\n",
        )
        with temporary, self.assertRaisesRegex(SystemExit, "proprietary"):
            self.validator.validate_repository(root)

    def test_publication_wrapper_enables_gate_for_github_workflow(self) -> None:
        self.assertIn('PUBLISH_WORKFLOW_NAME = "Publish signed HostPanel release"', self.wrapper)
        self.assertIn('os.environ.get("GITHUB_WORKFLOW") == PUBLISH_WORKFLOW_NAME', self.wrapper)
        self.assertIn('os.environ.get("HP_REQUIRE_FINAL_LEGAL_TERMS") == "yes"', self.wrapper)
        self.assertIn('with_name("build_update_release_impl.py")', self.wrapper)

    def test_wrapper_blocks_before_builder_on_incomplete_terms(self) -> None:
        temporary, root = self.fixture(
            "# HostPanel\n\n**License:** Proprietary — see [LICENSE](LICENSE).\n",
            "HostPanel End User License Agreement\nEffective [DATE]\n",
        )
        with temporary:
            environment = os.environ.copy()
            environment["HP_REQUIRE_FINAL_LEGAL_TERMS"] = "yes"
            result = subprocess.run(
                ["python3", str(WRAPPER_PATH), "--repository-root", str(root), "--print-version"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unresolved legal placeholders", result.stderr)
        self.assertNotIn("signed source archive", result.stderr)


if __name__ == "__main__":
    unittest.main()
