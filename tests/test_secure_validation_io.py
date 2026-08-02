from __future__ import annotations

import importlib.util
import os
import pathlib
import socket
import stat
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tools" / "secure-validation-io.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("secure_validation_io_test", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load secure validation helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SecureValidationIoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()
        cls.uid = os.getuid()

    def trusted_root(self, parent: pathlib.Path) -> pathlib.Path:
        os.chmod(parent, 0o700)
        return parent

    def test_state_write_read_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            state_dir = root / "state"
            self.helper.ensure_directory(
                state_dir, 0o700, owner_uid=self.uid, trusted_root=root
            )
            path = state_dir / "pre-reboot.boot-id"
            self.helper.write_state(
                path, b"first\n", owner_uid=self.uid, trusted_root=root
            )
            self.assertEqual(
                self.helper.read_state(path, owner_uid=self.uid, trusted_root=root),
                b"first\n",
            )
            first = path.stat()
            self.helper.write_state(
                path, b"second\n", owner_uid=self.uid, trusted_root=root
            )
            second = path.stat()
            self.assertNotEqual(
                (first.st_dev, first.st_ino),
                (second.st_dev, second.st_ino),
            )
            self.assertEqual(stat.S_IMODE(second.st_mode), 0o600)
            self.assertEqual(second.st_nlink, 1)

    def test_state_rejects_symlink_fifo_socket_device_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            regular = state_dir / "regular"
            regular.write_text("value\n", encoding="utf-8")
            regular.chmod(0o600)

            symlink = state_dir / "symlink"
            symlink.symlink_to(regular.name)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.read_state(symlink, owner_uid=self.uid, trusted_root=root)

            fifo = state_dir / "fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.read_state(fifo, owner_uid=self.uid, trusted_root=root)

            socket_path = state_dir / "socket"
            server = socket.socket(socket.AF_UNIX)
            try:
                server.bind(str(socket_path))
                with self.assertRaises(self.helper.ValidationIoError):
                    self.helper.read_state(
                        socket_path, owner_uid=self.uid, trusted_root=root
                    )
            finally:
                server.close()

            hardlink = state_dir / "hardlink"
            os.link(regular, hardlink)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.read_state(regular, owner_uid=self.uid, trusted_root=root)

            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.read_state(
                    pathlib.Path("/dev/null"),
                    owner_uid=0,
                    trusted_root=pathlib.Path("/"),
                )

    def test_state_write_rejects_unsafe_existing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            target = state_dir / "state.txt"
            regular = state_dir / "regular.txt"
            regular.write_text("value\n", encoding="utf-8")
            regular.chmod(0o600)
            target.symlink_to(regular.name)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.write_state(
                    target, b"new\n", owner_uid=self.uid, trusted_root=root
                )
            target.unlink()
            os.mkfifo(target, 0o600)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.write_state(
                    target, b"new\n", owner_uid=self.uid, trusted_root=root
                )
            target.unlink()
            os.link(regular, target)
            with self.assertRaisesRegex(self.helper.ValidationIoError, "hard link"):
                self.helper.write_state(
                    target, b"new\n", owner_uid=self.uid, trusted_root=root
                )

    def test_state_detects_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            path = state_dir / "state.txt"
            original_replace = self.helper.os.replace
            replaced = False

            def replace_with_parent_swap(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    os.rename(state_dir, root / "state-detached")
                    state_dir.mkdir(mode=0o700)
                return original_replace(
                    src,
                    dst,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            self.helper.os.replace = replace_with_parent_swap
            try:
                with self.assertRaisesRegex(
                    self.helper.ValidationIoError,
                    "trusted directory path changed",
                ):
                    self.helper.write_state(
                        path, b"value\n", owner_uid=self.uid, trusted_root=root
                    )
            finally:
                self.helper.os.replace = original_replace

    def test_parent_symlink_and_writable_parent_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            safe = root / "safe"
            safe.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(safe.name)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.ensure_directory(
                    link, 0o700, owner_uid=self.uid, trusted_root=root
                )
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o700)
            unsafe.chmod(0o777)
            with self.assertRaisesRegex(
                self.helper.ValidationIoError,
                "group/world writable",
            ):
                self.helper.open_trusted_directory(
                    unsafe, owner_uid=self.uid, trusted_root=root
                )

    def test_hook_executes_opened_descriptor_after_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            hooks = root / "hooks"
            hooks.mkdir(mode=0o700)
            hook = hooks / "hook.sh"
            output = root / "output.txt"
            hook.write_text(
                '#!/bin/sh\nprintf original > "$HP_TEST_OUTPUT"\n',
                encoding="utf-8",
            )
            hook.chmod(0o700)

            pid = os.fork()
            if pid == 0:
                try:
                    def swap() -> None:
                        os.rename(hook, hooks / "original.sh")
                        hook.write_text(
                            '#!/bin/sh\nprintf replacement > "$HP_TEST_OUTPUT"\n',
                            encoding="utf-8",
                        )
                        hook.chmod(0o700)

                    self.helper.execute_hook(
                        hook,
                        owner_uid=self.uid,
                        trusted_root=root,
                        environment={"HP_TEST_OUTPUT": str(output)},
                        before_exec=swap,
                    )
                except BaseException:
                    os._exit(125)
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_hook_rejects_links_nonregular_and_unsafe_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            hooks = root / "hooks"
            hooks.mkdir(mode=0o700)
            regular = hooks / "regular.sh"
            regular.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            regular.chmod(0o700)
            link = hooks / "link.sh"
            link.symlink_to(regular.name)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.open_hook_descriptor(
                    link, owner_uid=self.uid, trusted_root=root
                )
            hardlink = hooks / "hard.sh"
            os.link(regular, hardlink)
            with self.assertRaisesRegex(self.helper.ValidationIoError, "hard link"):
                self.helper.open_hook_descriptor(
                    regular, owner_uid=self.uid, trusted_root=root
                )
            hardlink.unlink()
            regular.chmod(0o722)
            with self.assertRaisesRegex(
                self.helper.ValidationIoError,
                "group/world writable",
            ):
                self.helper.open_hook_descriptor(
                    regular, owner_uid=self.uid, trusted_root=root
                )
            regular.unlink()
            fifo = hooks / "fifo"
            os.mkfifo(fifo, 0o700)
            with self.assertRaises(self.helper.ValidationIoError):
                self.helper.open_hook_descriptor(
                    fifo, owner_uid=self.uid, trusted_root=root
                )

    def test_report_is_exclusive_private_and_fsynced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            report_dir = root / "reports"
            status = self.helper.run_with_report(
                report_dir,
                ["/bin/sh", "-c", "printf 'validator output\\n'"],
                owner_uid=self.uid,
                trusted_root=root,
            )
            self.assertEqual(status, 0)
            reports = list(report_dir.glob("hostpanel-production-validation-*.log"))
            self.assertEqual(len(reports), 1)
            report = reports[0]
            self.assertEqual(report.read_text(encoding="utf-8"), "validator output\n")
            metadata = report.stat()
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)

    def test_report_detects_path_and_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            report_dir = root / "reports"
            report_dir.mkdir(mode=0o755)
            command = [
                sys.executable,
                "-c",
                (
                    "import os,pathlib; "
                    "p=pathlib.Path(os.environ['HP_VALIDATION_REPORT_PATH']); "
                    "old=p.parent.with_name('reports-detached'); "
                    "os.rename(p.parent,old); p.parent.mkdir(mode=0o755); "
                    "print('output')"
                ),
            ]
            with self.assertRaisesRegex(
                self.helper.ValidationIoError,
                "trusted directory path changed",
            ):
                self.helper.run_with_report(
                    report_dir,
                    command,
                    owner_uid=self.uid,
                    trusted_root=root,
                )

    def test_report_detects_final_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.trusted_root(pathlib.Path(temporary_name))
            report_dir = root / "reports"
            command = [
                sys.executable,
                "-c",
                (
                    "import os,pathlib; "
                    "p=pathlib.Path(os.environ['HP_VALIDATION_REPORT_PATH']); "
                    "p.unlink(); p.symlink_to('/dev/null'); print('output')"
                ),
            ]
            with self.assertRaisesRegex(
                self.helper.ValidationIoError,
                "validation report .*hard link|validation report path changed",
            ):
                self.helper.run_with_report(
                    report_dir,
                    command,
                    owner_uid=self.uid,
                    trusted_root=root,
                )

    def test_source_compiles(self) -> None:
        compile(
            HELPER_PATH.read_text(encoding="utf-8"),
            str(HELPER_PATH),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
