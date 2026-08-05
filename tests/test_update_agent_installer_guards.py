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

    def test_configuration_links_and_hardlinks_are_rejected_before_publication(self) -> None:
        symlink_guard = 'if [[ -L "$CONFIG" ]]; then'
        link_count_guard = (
            '[[ "$(stat -c %u:%g:%h -- "$CONFIG")" == 0:0:1 ]]'
        )
        first_mutation = (
            'install -d -o root -g root -m 755 /opt/hostpanel/tools'
        )
        for marker in (symlink_guard, link_count_guard):
            self.assertIn(marker, self.source)
            self.assertLess(
                self.source.index(marker), self.source.index(first_mutation)
            )

    def test_token_links_and_hardlinks_are_rejected_before_publication(self) -> None:
        symlink_guard = 'if [[ -L "$TOKEN_FILE" ]]; then'
        link_count_guard = (
            '[[ "$(stat -c %u:%g:%h -- "$TOKEN_FILE")" == 0:0:1 ]]'
        )
        first_mutation = (
            'install -d -o root -g root -m 755 /opt/hostpanel/tools'
        )
        for marker in (symlink_guard, link_count_guard):
            self.assertIn(marker, self.source)
            self.assertLess(
                self.source.index(marker), self.source.index(first_mutation)
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
