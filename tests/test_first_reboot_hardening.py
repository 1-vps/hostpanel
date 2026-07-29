import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class FirstRebootHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile(prefix="hostpanel-first-reboot-", delete=False) as handle:
            cls.generated_path = pathlib.Path(handle.name)
        result = subprocess.run(
            ["python3", str(HARDENER), str(BASE), str(cls.generated_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout)
        cls.generated = cls.generated_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.generated_path.unlink(missing_ok=True)

    def test_hostpanel_orders_control_plane_units_before_start(self):
        self.assertIn('SERVICE_WANTS="network-online.target"', self.generated)
        self.assertIn('SERVICE_WANTS+=" mariadb.service postgresql.service"', self.generated)
        self.assertIn('REDIS_UNIT="$(svc redis).service"', self.generated)
        self.assertIn('SERVICE_AFTER+=" $REDIS_UNIT"', self.generated)
        self.assertIn('SERVICE_WANTS+=" $REDIS_UNIT"', self.generated)
        self.assertIn('Wants=$SERVICE_WANTS', self.generated)

    def test_root_quota_mount_is_not_modified_implicitly(self):
        self.assertIn(
            'Automatic fstab quota changes are disabled', self.generated
        )
        self.assertIn(
            'needs usrquota in its mount options — update fstab, remount, then initialise quotas',
            self.generated,
        )
        self.assertNotIn('/etc/fstab.hostpanel.bak', self.generated)
        self.assertNotIn('{$4=$4",usrquota"}', self.generated)

    def test_openipmi_is_disabled_only_without_a_local_interface(self):
        self.assertIn(
            'systemctl list-unit-files openipmi.service', self.generated
        )
        self.assertIn("! compgen -G '/dev/ipmi*'", self.generated)
        self.assertIn('[[ ! -d /dev/ipmi ]]', self.generated)
        self.assertIn(
            'systemctl disable --now openipmi.service', self.generated
        )
        self.assertIn('systemctl reset-failed openipmi.service', self.generated)

    def test_plugin_tree_is_readable_but_not_writable_by_panel(self):
        restrictive = self.generated.index(
            'chmod 750 /var/lib/hostpanel/wp-smart /var/lib/hostpanel/disaster-restore'
        )
        ownership = self.generated.index(
            'chown -R root:"$PANEL_USER" /opt/hostpanel/plugins', restrictive
        )
        directories = self.generated.index(
            'find /opt/hostpanel/plugins -type d -exec chmod 750 {} +', ownership
        )
        files = self.generated.index(
            'find /opt/hostpanel/plugins -type f -exec chmod 640 {} +', directories
        )
        self.assertLess(restrictive, ownership)
        self.assertLess(ownership, directories)
        self.assertLess(directories, files)
        self.assertNotIn(
            'chown -R "$PANEL_USER:$PANEL_USER" /opt/hostpanel/plugins',
            self.generated,
        )

    def test_generated_installer_has_valid_bash_syntax(self):
        subprocess.run(["bash", "-n", str(self.generated_path)], check=True)


if __name__ == "__main__":
    unittest.main()
