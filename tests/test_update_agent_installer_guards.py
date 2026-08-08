from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'tools' / 'install-update-agent.sh'


class UpdateAgentInstallerGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = INSTALLER.read_text(encoding='utf-8')

    def test_environment_is_sanitized_before_any_privileged_command(self) -> None:
        root_check = '[[ ${EUID:-$(id -u)} -eq 0 ]]'
        markers = (
            'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'export PATH',
            'umask 077',
            'unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH',
        )
        for marker in markers:
            self.assertIn(marker, self.source)
            self.assertLess(self.source.index(marker), self.source.index(root_check))

    def test_configuration_links_and_unsafe_modes_are_rejected_before_publication(self) -> None:
        markers = (
            'if [[ -L "$CONFIG" ]]; then',
            '[[ "$(stat -c %u:%g:%h:%a -- "$CONFIG")" == 0:0:1:600 ]]',
        )
        first_mutation = (
            'install -d -o root -g root -m 755 /opt/hostpanel/tools'
        )
        for marker in markers:
            self.assertIn(marker, self.source)
            self.assertLess(
                self.source.index(marker), self.source.index(first_mutation)
            )

    def test_token_links_and_unsafe_modes_are_rejected_before_publication(self) -> None:
        markers = (
            'if [[ -L "$TOKEN_FILE" ]]; then',
            '[[ "$(stat -c %u:%g:%h:%a -- "$TOKEN_FILE")" == 0:0:1:600 ]]',
        )
        first_mutation = (
            'install -d -o root -g root -m 755 /opt/hostpanel/tools'
        )
        for marker in markers:
            self.assertIn(marker, self.source)
            self.assertLess(
                self.source.index(marker), self.source.index(first_mutation)
            )

    def test_every_existing_destination_is_preflighted_before_mutation(self) -> None:
        first_mutation = self.source.index(
            'install -d -o root -g root -m 755 /opt/hostpanel/tools'
        )
        markers = (
            'preflight_directory /opt/hostpanel 755',
            'preflight_directory /opt/hostpanel/tools 755',
            'preflight_directory /etc/hostpanel 700',
            'preflight_directory /var/lib/hostpanel 700',
            'preflight_directory /etc/systemd/system 755',
            'preflight_file /opt/hostpanel/tools/hostpanel-update-impl.py 644',
            'preflight_file /opt/hostpanel/tools/hostpanel-update 755',
            'preflight_file "/etc/hostpanel/$name" 644',
            'preflight_file /etc/hostpanel/update-keyring.json 600',
            'preflight_file /etc/systemd/system/hostpanel-update.service 644',
            'preflight_file /etc/systemd/system/hostpanel-update.timer 644',
        )
        for marker in markers:
            self.assertIn(marker, self.source)
            self.assertLess(self.source.index(marker), first_mutation)
        self.assertIn(
            '[[ "$(stat -c %u:%g:%h:%a -- "$path")" == '
            '"0:0:1:$expected_mode" ]]',
            self.source,
        )

    def test_configuration_migration_is_atomic_and_metadata_preserving(self) -> None:
        for marker in (
            "if payload and not payload.endswith(b'\\n'):",
            "payload += b'HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json\\n'",
            "write_flags |= getattr(os, 'O_NOFOLLOW', 0)",
            "os.replace(temporary, path)",
            "os.fsync(directory_fd)",
            "xattrs = {",
            "os.setxattr(out, name, value)",
        ):
            self.assertIn(marker, self.source)
        self.assertNotIn(
            "printf '%s\\n' 'HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json' >>\"$CONFIG\"",
            self.source,
        )

    def test_installer_publishes_separately_reviewed_entry_atomically(self) -> None:
        entry = (ROOT / 'tools' / 'hostpanel-update-entry.py').read_text(
            encoding='utf-8'
        )
        self.assertIn(
            'UPDATER_ENTRY="$SOURCE_ROOT/tools/hostpanel-update-entry.py"',
            self.source,
        )
        self.assertIn(
            'python3 - "$UPDATER_ENTRY" '
            '/opt/hostpanel/tools/hostpanel-update',
            self.source,
        )
        self.assertNotIn("ENTRY_PAYLOAD = r'''", self.source)
        self.assertIn("source_fd = os.open(source, flags)", self.source)
        self.assertIn("os.replace(temporary, destination)", self.source)
        self.assertIn("os.fsync(directory_fd)", self.source)
        self.assertIn('with safe_lock(pathlib.Path(args.lock_file))', entry)
        self.assertIn('os.O_NOFOLLOW', entry)
        self.assertNotIn('return _IMPL.main(argv)', entry)


if __name__ == '__main__':
    unittest.main()
