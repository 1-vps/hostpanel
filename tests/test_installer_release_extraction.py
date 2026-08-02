#!/usr/bin/env python3
from __future__ import annotations

import io
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "installer-hardening.yml"


class InstallerReleaseExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def extraction_script(self) -> str:
        marker = '              python3 - "$archive" <<PY\n'
        try:
            embedded = self.text.split(marker, 1)[1].split("\n          PY\n", 1)[0]
        except IndexError as exc:
            raise AssertionError("could not locate the embedded release extractor") from exc
        script = textwrap.dedent(embedded)
        self.assertIn('with tarfile.open(archive, "r:gz") as handle:', script)
        return (
            script.replace(
                'destination = pathlib.Path("/tmp/release")',
                "destination = pathlib.Path(sys.argv[2])",
            )
            .replace(
                'pathlib.Path("/tmp/hostpanel-release-root").write_text(',
                "pathlib.Path(sys.argv[3]).write_text(",
            )
        )

    def run_extractor(self, members: list[tarfile.TarInfo | tuple[str, bytes]]):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = pathlib.Path(temp_dir)
            archive = temp / "source.tar.gz"
            destination = temp / "release"
            root_file = temp / "release-root"
            script_file = temp / "extract.py"
            script_file.write_text(self.extraction_script(), encoding="utf-8")

            with tarfile.open(archive, "w:gz") as handle:
                for member in members:
                    if isinstance(member, tuple):
                        name, payload = member
                        info = tarfile.TarInfo(name)
                        info.size = len(payload)
                        handle.addfile(info, io.BytesIO(payload))
                    else:
                        handle.addfile(member)

            result = subprocess.run(
                [
                    sys.executable,
                    str(script_file),
                    str(archive),
                    str(destination),
                    str(root_file),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            extracted = {
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
            } if destination.exists() else set()
            root_value = root_file.read_text(encoding="utf-8") if root_file.exists() else ""
            return result, extracted, root_value

    def test_runtime_lock_requires_one_regular_signed_archive(self) -> None:
        self.assertIn('test ! -L SHA256SUMS', self.text)
        self.assertIn('mapfile -t archives', self.text)
        self.assertIn('test "${#archives[@]}" -eq 1', self.text)
        self.assertIn('test ! -L "$archive"', self.text)
        self.assertIn('test ! -L "$archive.sig"', self.text)
        self.assertIn('openssl pkeyutl -verify -pubin', self.text)

    def test_runtime_lock_extracts_source_fail_closed(self) -> None:
        self.assertNotIn('tar -xzf "$archive"', self.text)
        for contract in (
            'path.is_absolute() or ".." in path.parts',
            "member.issym() or member.islnk()",
            "not (member.isdir() or member.isfile())",
            "len(members) > 20000",
            "total > 200 * 1024 * 1024",
            "len(roots) != 1",
            "root.is_symlink()",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.text)
        self.assertIn('release_root=$(cat /tmp/hostpanel-release-root)', self.text)

    def test_release_is_verified_before_extraction(self) -> None:
        verify = self.text.index('openssl pkeyutl -verify -pubin')
        extract = self.text.index('with tarfile.open(archive, "r:gz") as handle:')
        requirements = self.text.index('test -s "$release_root/requirements.lock"')
        self.assertLess(verify, extract)
        self.assertLess(extract, requirements)

    def test_embedded_extractor_accepts_one_regular_root(self) -> None:
        result, extracted, root_value = self.run_extractor(
            [("hostpanel-v3.4.0/requirements.lock", b"locked\n")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hostpanel-v3.4.0/requirements.lock", extracted)
        self.assertTrue(root_value.endswith("/release/hostpanel-v3.4.0"), root_value)

    def test_embedded_extractor_rejects_unsafe_archives(self) -> None:
        linked = tarfile.TarInfo("hostpanel-v3.4.0/link")
        linked.type = tarfile.SYMTYPE
        linked.linkname = "../../etc/passwd"
        cases = {
            "traversal": [("../escape", b"no")],
            "multiple roots": [("one/file", b"1"), ("two/file", b"2")],
            "symbolic link": [linked],
        }
        for name, members in cases.items():
            with self.subTest(name=name):
                result, extracted, root_value = self.run_extractor(members)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(root_value)
                self.assertNotIn("escape", extracted)


if __name__ == "__main__":
    unittest.main()
