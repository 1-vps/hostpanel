from __future__ import annotations

from _updater_atomic_entry_impl import *  # noqa: F401,F403
import _updater_atomic_entry_impl as _impl


def _test_installer_publishes_reviewed_entry_and_non_executable_implementation(
    self,
) -> None:
    installer = (_impl.TOOLS / 'install-update-agent.sh').read_text(
        encoding='utf-8'
    )
    self.assertIn(
        'UPDATER_IMPL="$SOURCE_ROOT/tools/hostpanel-update.py"',
        installer,
    )
    self.assertIn(
        'UPDATER_ENTRY="$SOURCE_ROOT/tools/hostpanel-update-entry.py"',
        installer,
    )
    self.assertIn(
        '"$UPDATER_IMPL" /opt/hostpanel/tools/hostpanel-update-impl.py',
        installer,
    )
    self.assertIn(
        'python3 - "$UPDATER_ENTRY" /opt/hostpanel/tools/hostpanel-update',
        installer,
    )
    self.assertNotIn("ENTRY_PAYLOAD = r'''", installer)
    self.assertIn('source_fd = os.open(source, flags)', installer)
    self.assertIn('os.replace(temporary, destination)', installer)
    self.assertIn('os.fsync(directory_fd)', installer)
    self.assertIn('install -o root -g root -m 644', installer)


_impl.UpdaterAtomicEntryTests.test_installer_embeds_entry_and_non_executable_implementation = (
    _test_installer_publishes_reviewed_entry_and_non_executable_implementation
)
UpdaterAtomicEntryTests = _impl.UpdaterAtomicEntryTests
