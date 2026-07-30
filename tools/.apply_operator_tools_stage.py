#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


impl = pathlib.Path("tools/harden_install_impl.py")
text = impl.read_text(encoding="utf-8")
text = replace_once(
    text,
    "COMMON_PACKAGES=(openssl rsync acl gnupg sqlite3 needrestart inotify-tools smartmontools prometheus-node-exporter iproute2 git ca-certificates python3 python3-venv python3-pip curl ufw fail2ban unzip sudo nginx openssh-server cron tar gzip util-linux hostname)",
    "COMMON_PACKAGES=(openssl rsync acl gnupg sqlite3 needrestart inotify-tools smartmontools prometheus-node-exporter iproute2 git ca-certificates python3 python3-venv python3-pip curl ufw fail2ban unzip sudo nginx openssh-server cron tar gzip util-linux hostname btop nano plocate)",
    "operator common packages",
)
text = replace_once(
    text,
    "    cron)                   printf 'cronie' ;;\n    inotify-tools)          printf 'inotify-tools' ;;",
    "    cron)                   printf 'cronie' ;;\n    plocate)\n      if pkg_available mlocate; then printf 'mlocate'; else printf 'plocate'; fi ;;\n    inotify-tools)          printf 'inotify-tools' ;;",
    "RHEL locate package mapping",
)
text = replace_once(
    text,
    '"RHEL cron package mapping",',
    '"RHEL operator tool package mapping",',
    "operator mapping label",
)

extra_transforms = textwrap.indent(
    textwrap.dedent(
        """\
text = _replace_once(
    text,
    '''readarray -t OPTIONAL_PACKAGES < <(pkg_map needrestart smartmontools prometheus-node-exporter podman-compose)''',
    '''readarray -t OPTIONAL_PACKAGES < <(pkg_map needrestart smartmontools prometheus-node-exporter podman-compose btop)''',
    "optional btop availability",
)
text = _replace_once(
    text,
    '''ok "role packages installed"''',
    '''for utility in locate updatedb nano; do
  command -v "$utility" >/dev/null 2>&1 \\
    || die "Required operator utility is unavailable after package installation: $utility"
done
if pkg_available "$(pkg_name btop)"; then
  command -v btop >/dev/null 2>&1 \\
    || die "btop package is available but the btop command is missing after installation"
fi
ok "role packages installed and operator utilities validated"''',
    "operator utility validation",
)

"""
    ),
    "    ",
)
dovecot_marker = "    text = _replace_once(\n        text,\n        '''passdb { driver = passwd-file; args = /etc/dovecot/users }"
text = replace_once(
    text,
    dovecot_marker,
    extra_transforms + dovecot_marker,
    "operator validation transforms",
)
impl.write_text(text, encoding="utf-8")


tests = pathlib.Path("tests/test_installer_hardening.py")
text = tests.read_text(encoding="utf-8")
method = textwrap.indent(
    textwrap.dedent(
        """\
def test_operator_baseline_tools_are_installed_and_validated(self):
    self.assertIn('hostname btop nano plocate)', self.installer)
    self.assertIn("if pkg_available mlocate; then printf 'mlocate'; else printf 'plocate'; fi", self.installer)
    self.assertIn('pkg_map needrestart smartmontools prometheus-node-exporter podman-compose btop', self.installer)
    for utility in ('locate', 'updatedb', 'nano'):
        self.assertIn(f'command -v "$utility"', self.installer)
    self.assertIn('command -v btop', self.installer)
    self.assertIn('role packages installed and operator utilities validated', self.installer)

"""
    ),
    "    ",
)
marker = "    def test_bootstrap_has_independent_trust_root(self):\n"
text = replace_once(text, marker, method + marker, "operator tool regression")
tests.write_text(text, encoding="utf-8")

blob = subprocess.run(
    ["git", "hash-object", "--no-filters", str(impl)],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
for filename in ("tools/harden_install.py", "tests/test_post_install_health.py"):
    path = pathlib.Path(filename)
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'EXPECTED_IMPL_BLOB = "[0-9a-f]{40}"',
        f'EXPECTED_IMPL_BLOB = "{blob}"',
        source,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"could not repin implementation blob in {filename}")
    path.write_text(updated, encoding="utf-8")
