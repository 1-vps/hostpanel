from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'tools' / 'install-update-agent.sh'


class UpdateAgentInstallerGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = INSTALLER.read_text(encoding='utf-8')

    def test_configuration_links_and_hardlinks_are_rejected_before_write(self) -> None:
        symlink_guard = 'if [[ -L "$CONFIG" ]]; then'
        link_count_guard = (
            'if [[ -e "$CONFIG" && "$(stat -c %u:%g:%h -- '
            '"$CONFIG")" != 0:0:1 ]]; then'
        )
        first_write = 'cat >"$CONFIG"'
        self.assertIn(symlink_guard, self.source)
        self.assertIn(link_count_guard, self.source)
        self.assertLess(self.source.index(symlink_guard), self.source.index(first_write))
        self.assertLess(self.source.index(link_count_guard), self.source.index(first_write))

    def test_token_links_and_hardlinks_are_rejected_before_mutation(self) -> None:
        symlink_guard = 'if [[ -L "$TOKEN_FILE" ]]; then'
        link_count_guard = (
            'if [[ -e "$TOKEN_FILE" && "$(stat -c %u:%g:%h -- '
            '"$TOKEN_FILE")" != 0:0:1 ]]; then'
        )
        first_mutation = 'chown root:root "$TOKEN_FILE"'
        self.assertIn(symlink_guard, self.source)
        self.assertIn(link_count_guard, self.source)
        self.assertLess(
            self.source.index(symlink_guard),
            self.source.index(first_mutation),
        )
        self.assertLess(
            self.source.index(link_count_guard),
            self.source.index(first_mutation),
        )

    def test_embedded_updater_uses_safe_lock_boundary(self) -> None:
        entry = (ROOT / 'tools' / 'hostpanel-update-entry.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("ENTRY_PAYLOAD = r'''" + entry + "'''", self.source)
        self.assertIn('with safe_lock(pathlib.Path(args.lock_file))', entry)
        self.assertIn('os.O_NOFOLLOW', entry)
        self.assertNotIn('return _IMPL.main(argv)', entry)


if __name__ == '__main__':
    unittest.main()
