from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ui-audit.yml"
PHP_PROBE = ROOT / "tests" / "php-runtime-compat.php"


def generated_installer() -> str:
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "install.generated.sh"
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "tools" / "harden_install.py"),
                str(ROOT / "install.base.sh"),
                str(output),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout)
        subprocess.run(["bash", "-n", str(output)], check=True)
        return output.read_text(encoding="utf-8")


class PhpRuntimeSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = generated_installer()
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.probe = PHP_PROBE.read_text(encoding="utf-8")

    def test_installer_requests_broad_php_support_for_every_branch(self):
        self.assertIn("PHP_SUFFIXES=(fpm cli common mysql curl", self.installer)
        self.assertIn("zstd xsl enchant odbc)", self.installer)
        self.assertIn("uuid event)", self.installer)
        self.assertIn("printf 'php%s-%s\\n' \"$version\" \"$suffix\"", self.installer)
        self.assertIn("xsl) printf 'php%s-xml\\n'", self.installer)
        self.assertIn("event|xdebug|pcov", self.installer)

    def test_installer_fails_closed_without_core_cli_and_fpm_capabilities(self):
        for contract in (
            'PHP $version CLI is missing required loaded module',
            'PHP $version FPM is missing required loaded module',
            'PHP $version CLI is missing OPcache',
            'PHP $version FPM is missing OPcache',
            'curl dom exif fileinfo filter gd iconv intl json mbstring openssl PDO',
            'pdo_mysql pdo_pgsql pdo_sqlite',
            'SimpleXML sodium soap tokenizer xml xmlreader xmlwriter zip mysqli',
        ):
            self.assertIn(contract, self.installer)

    def test_installer_runtime_checks_bcrypt_https_sodium_and_csprng(self):
        for contract in (
            "PASSWORD_BCRYPT",
            '["cost" => 12]',
            'password_verify($password, $hash)',
            'curl_version()',
            'in_array("https"',
            'sodium_crypto_pwhash',
            'random_bytes',
            'failed bcrypt, cURL HTTPS, sodium, or CSPRNG runtime validation',
        ):
            self.assertIn(contract, self.installer)
        self.assertIn('/etc/hostpanel/php-recommended-missing', self.installer)

    def test_php_probe_covers_real_tls_curl_and_bcrypt(self):
        for contract in (
            "CURLOPT_CAINFO",
            "CURLPROTO_HTTPS",
            "CURLINFO_RESPONSE_CODE",
            "PASSWORD_BCRYPT",
            "password_verify",
            "password_get_info",
            "bcrypt input exceeds 72 bytes",
        ):
            self.assertIn(contract, self.probe)
        for unsafe in (
            "CURLOPT_SSL_VERIFYPEER => false",
            "CURLOPT_SSL_VERIFYHOST => 0",
            "http://",
        ):
            self.assertNotIn(unsafe, self.probe)

    def test_ui_audit_executes_python_and_php_runtime_probes(self):
        self.assertIn("--require-hashes", self.workflow)
        self.assertIn("import bcrypt", self.workflow)
        self.assertIn("php-cli php-curl", self.workflow)
        self.assertIn("tests/php-runtime-compat.php", self.workflow)
        self.assertIn("openssl req -x509", self.workflow)
        self.assertIn("https://localhost:18443/health", self.workflow)


if __name__ == "__main__":
    unittest.main()
