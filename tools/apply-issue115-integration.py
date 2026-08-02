#!/usr/bin/env python3
# Apply the staged issue #115 integration changes in a checked-out branch.

from __future__ import annotations

import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(value: str, old: str, new: str, label: str) -> str:
    count = value.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label} shape: found {count}")
    return value.replace(old, new, 1)


def move_staged(staged: str, target: str, mode: int) -> None:
    source = ROOT / staged
    destination = ROOT / target
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"missing staged file: {staged}")
    os.replace(source, destination)
    os.chmod(destination, mode)


move_staged("tools/hostpanel-update.py.issue115", "tools/hostpanel-update.py", 0o755)
move_staged("tools/build-update-release.py.issue115", "tools/build-update-release.py", 0o755)
move_staged(
    "packaging/systemd/hostpanel-update.service.issue115",
    "packaging/systemd/hostpanel-update.service",
    0o644,
)
move_staged(
    "tools/install-update-agent.sh.issue115",
    "tools/install-update-agent.sh",
    0o755,
)

bootstrap = text("bootstrap-install.sh")
bootstrap_old = r'''[[ "${SOURCE_VERSION%%[-+]*}" == "${ARCHIVE_RELEASE_ID%%[-+]*}" ]] \
  || die "Verified source archive VERSION is incompatible with its signed filename"

verify_commit_file(){'''
bootstrap_new = r'''[[ "${SOURCE_VERSION%%[-+]*}" == "${ARCHIVE_RELEASE_ID%%[-+]*}" ]] \
  || die "Verified source archive VERSION is incompatible with its signed filename"

RELEASE_VERSION_FILE="$CHECKOUT/RELEASE_VERSION"
[[ -f "$RELEASE_VERSION_FILE" && ! -L "$RELEASE_VERSION_FILE" ]] \
  || die "Reviewed commit is missing a safe RELEASE_VERSION"
RELEASE_VERSION_EXPECTED="$(git -C "$CHECKOUT" rev-parse "$FETCHED_COMMIT:RELEASE_VERSION" 2>/dev/null)" \
  || die "Reviewed commit does not contain RELEASE_VERSION"
RELEASE_VERSION_ACTUAL="$(git -C "$CHECKOUT" hash-object "$RELEASE_VERSION_FILE")" \
  || die "Could not hash reviewed RELEASE_VERSION"
[[ "$RELEASE_VERSION_ACTUAL" == "$RELEASE_VERSION_EXPECTED" ]] \
  || die "RELEASE_VERSION does not match its reviewed Git object"
DEPLOY_VERSION="$(tr -d '[:space:]' <"$RELEASE_VERSION_FILE")"
[[ "$DEPLOY_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
  || die "RELEASE_VERSION must be a final strict semantic version"
python3 - "$SOURCE_VERSION" "$DEPLOY_VERSION" <<'PYVERSION'
import re
import sys

pattern = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
source_match = pattern.fullmatch(sys.argv[1])
deploy_match = pattern.fullmatch(sys.argv[2])
if source_match is None or deploy_match is None:
    raise SystemExit("base and deploy versions must be final strict semantic versions")
source = tuple(int(part) for part in source_match.groups())
deploy = tuple(int(part) for part in deploy_match.groups())
if not source < deploy:
    raise SystemExit("RELEASE_VERSION must be greater than the signed source VERSION")
PYVERSION
printf '%s\n' "$DEPLOY_VERSION" >"$SOURCE_ROOT/VERSION"
chmod 0644 "$SOURCE_ROOT/VERSION"

verify_commit_file(){'''
bootstrap = replace_once(bootstrap, bootstrap_old, bootstrap_new, "bootstrap release version")
write("bootstrap-install.sh", bootstrap)

harness = text("tools/run-qemu-vm-acceptance.sh")
harness = replace_once(
    harness,
    'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0}"',
    'DEFAULT_EXPECTED_VERSION="$(tr -d \'[:space:]\' <"$REPO_ROOT/RELEASE_VERSION")"\n'
    'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-$DEFAULT_EXPECTED_VERSION}"',
    "QEMU expected version",
)
write("tools/run-qemu-vm-acceptance.sh", harness)

validator = text("tools/validate-production-vm.sh")
validator = replace_once(
    validator,
    'EXPECTED_VERSION="${HP_EXPECTED_VERSION:-3.4.0}"',
    'EXPECTED_VERSION="${HP_EXPECTED_VERSION:-3.4.1}"',
    "validator expected version",
)
write("tools/validate-production-vm.sh", validator)

qemu = text(".github/workflows/qemu-vm-acceptance.yml")
qemu = replace_once(
    qemu,
    "      - releases/update.pub\n",
    "      - releases/update.pub\n      - releases/update-keyring.json\n",
    "QEMU keyring path",
)
qemu = replace_once(
    qemu,
    "      - bootstrap-install.sh\n",
    "      - bootstrap-install.sh\n      - RELEASE_VERSION\n",
    "QEMU release version path",
)
qemu = replace_once(
    qemu,
    "      - tests/test_github_update_pipeline.py\n",
    "      - tests/test_github_update_pipeline.py\n      - tests/test_updater_issue115.py\n",
    "QEMU issue 115 test path",
)
qemu = replace_once(
    qemu,
    "      HP_QEMU_EXPECTED_VERSION: 3.4.0\n",
    "",
    "QEMU hard-coded version",
)
qemu = replace_once(
    qemu,
    "          python3 -m unittest discover -s tests -p 'test_github_update_pipeline.py' -v\n",
    "          python3 -m unittest discover -s tests -p 'test_github_update_pipeline.py' -v\n"
    "          python3 -m unittest discover -s tests -p 'test_updater_issue115.py' -v\n",
    "QEMU issue 115 test execution",
)
write(".github/workflows/qemu-vm-acceptance.yml", qemu)

vps = text(".github/workflows/vps-acceptance.yml")
vps = replace_once(
    vps,
    "      EXPECTED_VERSION: 3.4.0\n",
    "",
    "VPS hard-coded version",
)
vps = replace_once(
    vps,
    "      - name: Prepare fail-closed provider evidence\n",
    '''      - name: Bind expected version to reviewed release
        run: |
          set -euo pipefail
          EXPECTED_VERSION="$(tr -d '[:space:]' < RELEASE_VERSION)"
          [[ "$EXPECTED_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
            echo 'RELEASE_VERSION must be a final strict semantic version.' >&2
            exit 1
          }
          printf 'EXPECTED_VERSION=%s\n' "$EXPECTED_VERSION" >> "$GITHUB_ENV"

      - name: Prepare fail-closed provider evidence
''',
    "VPS release version binding",
)
write(".github/workflows/vps-acceptance.yml", vps)

publish = text(".github/workflows/publish-release.yml")
publish = replace_once(
    publish,
    "      - bootstrap-install.sh\n",
    "      - bootstrap-install.sh\n      - RELEASE_VERSION\n",
    "publish RELEASE_VERSION path",
)
publish = replace_once(
    publish,
    "      - releases/update.pub\n",
    "      - releases/update.pub\n      - releases/update-keyring.json\n",
    "publish keyring path",
)
write(".github/workflows/publish-release.yml", publish)

qemu_test = text("tests/test_qemu_default_version.py")
old_method = '''    def test_local_harness_default_matches_ci_release_version(self):
        harness = HARNESS.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0}"',
            harness,
        )
        self.assertIn("HP_QEMU_EXPECTED_VERSION: 3.4.0", workflow)
        self.assertNotIn("3.4.0-hardened-r6", harness)
'''
new_method = '''    def test_local_harness_default_matches_ci_release_version(self):
        harness = HARNESS.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        release_version = (ROOT / "RELEASE_VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(release_version, "3.4.1")
        self.assertIn(
            'DEFAULT_EXPECTED_VERSION="$(tr -d \'[:space:]\' <"$REPO_ROOT/RELEASE_VERSION")"',
            harness,
        )
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-$DEFAULT_EXPECTED_VERSION}"',
            harness,
        )
        self.assertNotIn("HP_QEMU_EXPECTED_VERSION: 3.4.0", workflow)
        self.assertIn("- RELEASE_VERSION", workflow)
'''
qemu_test = replace_once(qemu_test, old_method, new_method, "QEMU default-version test")
write("tests/test_qemu_default_version.py", qemu_test)

private_docs_test = text("tests/test_private_repo_documentation.py")
private_docs_test = replace_once(
    private_docs_test,
    '                self.assertIn("3.4.0", text)',
    '                self.assertIn("3.4.1", text)',
    "documentation release identifier",
)
private_docs_test = replace_once(
    private_docs_test,
    '        self.assertIn("HP_EXPECTED_VERSION=3.4.0", configuration)',
    '        self.assertIn("HP_EXPECTED_VERSION=3.4.1", configuration)',
    "documentation expected version",
)
write("tests/test_private_repo_documentation.py", private_docs_test)

for doc_path in ("README.md", "SETUP.md", "CONFIGURATION.md", "PRODUCTION_READINESS.md"):
    value = text(doc_path)
    marker = "Current deployable overlay release: **3.4.1** (signed base source: `3.4.0`)."
    if marker not in value:
        value = value.rstrip() + "\n\n" + marker + "\n"
    if doc_path in {"SETUP.md", "CONFIGURATION.md"}:
        value = value.replace("HP_EXPECTED_VERSION=3.4.0", "HP_EXPECTED_VERSION=3.4.1")
    write(doc_path, value)

updates = r'''# Signed GitHub releases and automatic updates

HostPanel publishes a signed GitHub Release for the deployable version in
`RELEASE_VERSION`. The signed source archive remains the immutable base; every
reviewed overlay revision must increase `RELEASE_VERSION` above the base
archive's `VERSION`. Reusing the same deployable version fails the release
build.

## Publication boundary

`.github/workflows/publish-release.yml` runs from protected `main`, verifies the
signed base source, enforces all release-gate issues, runs the complete
regression suite, builds the exact reviewed commit, and enters the protected
`hostpanel-release` environment only for signing and publication.

Release inputs include `RELEASE_VERSION`, updater/runtime code, the public
keyring, installers, packaging, and the release workflow itself. Existing tags
and releases are accepted only when their commit, exact asset set, signatures,
manifest, archive digest, and provenance all match the reviewed commit.

## Trusted update configuration

The update service does not source configuration as a process environment.
`/etc/hostpanel/update-agent.conf` is parsed through a descriptor-bound,
root-owned mode-0600 reader and accepts only these keys:

```ini
HP_UPDATE_REPOSITORY=1-vps/hostpanel
HP_UPDATE_CHANNEL=stable
HP_UPDATE_TOKEN_FILE=/etc/hostpanel/github-update.token
HP_UPDATE_REQUIRE_TOKEN=yes
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json
HP_AUTO_UPDATE=yes
```

The token file must be root-owned, single-linked, mode `0600`, ASCII-only, and
contain no whitespace, including no trailing newline:

```bash
read -rsp 'GitHub update token: ' TOKEN; echo
printf '%s' "$TOKEN" | sudo tee /etc/hostpanel/github-update.token >/dev/null
unset TOKEN
sudo chown root:root /etc/hostpanel/github-update.token
sudo chmod 600 /etc/hostpanel/github-update.token
```

`stable` resolves GitHub's latest final release. `beta` inspects at most 20
GitHub releases and selects the highest valid signed prerelease version. Draft
flags, prerelease flags, tags, signed channel, and strict semantic-version form
must agree.

## Verification and transport

Every request and redirect must remain HTTPS and within the GitHub API/release
asset host set. Authorization is removed on cross-origin redirects. Duplicate
asset names, unsafe names, non-integer sizes, and advertised/downloaded size
mismatches fail closed.

The updater verifies, in order:

1. root ownership, exact mode, single link, stable descriptor/path identity,
   and bounded size for configuration, token, keyring, and key files;
2. a bounded keyring with SHA-256 key IDs and semantic activation/retirement
   windows;
3. the manifest signature using securely captured public-key bytes;
4. strict manifest shape, channel/version/tag/commit binding;
5. archive length, digest, and signature with the same key that signed the
   manifest;
6. archive path/type/mode/expansion bounds and extracted `VERSION`;
7. the existing reinstall, snapshot, health-check, and rollback path.

## Key rotation

Rotation is two-stage:

1. publish an old-key-signed release that adds the next public key to
   `releases/` and to `update-keyring.json`, with a future `activate_from`
   version while retaining the old key;
2. after that transition is installed, sign a later release with the new key;
3. keep an overlap window as needed, then set the old key's `retire_after`;
4. never remove the previous key before all supported clients have crossed the
   transition release.

The installer deploys the complete bounded keyring atomically with the updater.
The reinstall snapshot covers `/etc/hostpanel`, so rollback preserves the
previous trusted keyring.

## Manual operation and result codes

```bash
sudo /opt/hostpanel/tools/hostpanel-update --check
sudo /opt/hostpanel/tools/hostpanel-update --apply
sudo /opt/hostpanel/tools/hostpanel-update --dry-run
systemctl status hostpanel-update.timer
journalctl -u hostpanel-update.service
```

`--dry-run` downloads and verifies the complete manifest/archive/signature path
without requiring `--apply`. Exit `10` means a newer verified release is
available but was not applied. Exit `75` means another updater owns the lock.
The oneshot unit declares both expected states successful; verification and
installer failures remain service failures.

Status is written atomically to
`/var/lib/hostpanel/update-status.json`, including the signing key ID.
'''
write("UPDATES.md", updates)


def blob(path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "hash-object", "--no-filters", path],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


harden = text("tools/harden_install.py")
mapping = {
    "tools/hostpanel-update.py": blob("tools/hostpanel-update.py"),
    "tools/install-update-agent.sh": blob("tools/install-update-agent.sh"),
    "packaging/systemd/hostpanel-update.service": blob(
        "packaging/systemd/hostpanel-update.service"
    ),
    "packaging/systemd/hostpanel-update.timer": blob(
        "packaging/systemd/hostpanel-update.timer"
    ),
    "releases/update.pub": blob("releases/update.pub"),
    "releases/update-keyring.json": blob("releases/update-keyring.json"),
}
replacement = "EXPECTED_UPDATE_AGENT_BLOBS = {\n" + "".join(
    f'    "{path}": "{digest}",\n' for path, digest in mapping.items()
) + "}\n"
harden, count = re.subn(
    r"EXPECTED_UPDATE_AGENT_BLOBS = \{\n.*?\n\}\n",
    replacement,
    harden,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise SystemExit("could not update reviewed update-agent blob map")
write("tools/harden_install.py", harden)

bootstrap_blob = blob("bootstrap-install.sh")
validator_blob = blob("tools/validate-production-vm.sh")
for path in (
    "README.md",
    "SETUP.md",
    "CONFIGURATION.md",
    "PRODUCTION_READINESS.md",
    "tests/test_private_repo_documentation.py",
):
    value = text(path)
    value = value.replace("eae493681ce5eecd5ea61491f8e08e1f40938e08", bootstrap_blob)
    value = value.replace("2672271aacc0d85013765b3a7887fdec95518643", validator_blob)
    write(path, value)

for staged in ROOT.rglob("*.issue115"):
    raise SystemExit(f"staging file remained after integration: {staged.relative_to(ROOT)}")
