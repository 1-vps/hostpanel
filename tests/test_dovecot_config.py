import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class DovecotConfigTests(unittest.TestCase):
    def generated_installer(self) -> str:
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(generated)], check=True)
            return generated.read_text(encoding="utf-8")

    def test_generated_mail_profiles_use_valid_multiline_blocks(self):
        text = self.generated_installer()
        self.assertNotIn(
            "passdb { driver = passwd-file; args = /etc/dovecot/users }",
            text,
        )
        self.assertNotIn(
            "userdb { driver = passwd-file; args = /etc/dovecot/users }",
            text,
        )
        self.assertEqual(
            text.count(
                "passdb {\n  driver = passwd-file\n  args = /etc/dovecot/users\n}"
            ),
            2,
        )
        self.assertEqual(
            text.count(
                "userdb {\n  driver = passwd-file\n  args = /etc/dovecot/users\n}"
            ),
            2,
        )

    def test_sieve_protocols_use_valid_multiline_blocks(self):
        text = self.generated_installer()
        self.assertNotIn(
            "protocol imap { mail_plugins = $mail_plugins imap_sieve }",
            text,
        )
        self.assertNotIn(
            "protocol lda { mail_plugins = $mail_plugins sieve }",
            text,
        )
        self.assertNotIn(
            "protocol lmtp { mail_plugins = $mail_plugins sieve }",
            text,
        )
        self.assertEqual(
            text.count(
                "protocol imap {\n  mail_plugins = $mail_plugins imap_sieve\n}"
            ),
            1,
        )
        self.assertEqual(
            text.count("protocol lda {\n  mail_plugins = $mail_plugins sieve\n}"),
            1,
        )
        self.assertEqual(
            text.count("protocol lmtp {\n  mail_plugins = $mail_plugins sieve\n}"),
            1,
        )

    def test_sieve_compilation_runs_after_plugin_configuration(self):
        text = self.generated_installer()
        config = text.index("cat >/etc/dovecot/conf.d/90-sieve-hostpanel.conf")
        validate = text.index(
            'doveconf -n >>"$LOG" 2>&1 || die "Dovecot IMAPSieve configuration is invalid"'
        )
        plugin_args = text.index("SIEVEC_PLUGIN_ARGS=(")
        spam = text.index(
            '/usr/bin/sievec "${SIEVEC_PLUGIN_ARGS[@]}" /usr/lib/dovecot/sieve/report-spam.sieve'
        )
        ham = text.index(
            '/usr/bin/sievec "${SIEVEC_PLUGIN_ARGS[@]}" /usr/lib/dovecot/sieve/report-ham.sieve'
        )
        restart = text.index('systemctl restart dovecot >>"$LOG" 2>&1')
        self.assertLess(config, validate)
        self.assertLess(validate, plugin_args)
        self.assertLess(plugin_args, spam)
        self.assertLess(spam, ham)
        self.assertLess(ham, restart)
        self.assertIn(
            "-o 'sieve_plugins=sieve_imapsieve sieve_extprograms'",
            text,
        )
        self.assertIn(
            "-o 'sieve_global_extensions=+vnd.dovecot.pipe +vnd.dovecot.environment'",
            text,
        )
        self.assertEqual(text.count('"${SIEVEC_PLUGIN_ARGS[@]}"'), 2)
        self.assertIn("Could not compile the spam-learning Sieve rule", text)
        self.assertIn("Could not compile the ham-learning Sieve rule", text)

    def test_hardener_fails_closed_on_reviewed_source_shapes(self):
        hardener = HARDENER.read_text(encoding="utf-8")
        self.assertIn('label == "Dovecot passwd-file block syntax"', hardener)
        self.assertIn("expected = 2", hardener)
        self.assertIn("Dovecot IMAP plugin block syntax", hardener)
        self.assertIn("Dovecot LDA plugin block syntax", hardener)
        self.assertIn("Dovecot LMTP plugin block syntax", hardener)
        self.assertIn("defer Sieve compilation until plugin configuration", hardener)
        self.assertIn("compile Sieve after plugin configuration", hardener)
        self.assertIn("SIEVEC_PLUGIN_ARGS", hardener)


if __name__ == "__main__":
    unittest.main()
