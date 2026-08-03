"""Repository trust adapter for MongoDB Community 8.0."""
from __future__ import annotations

import pathlib
import shutil
import stat
import tempfile

import hostpanel_build_extras as base
from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import run_command

MONGODB_RPM_KEY = pathlib.Path('/etc/pki/rpm-gpg/MONGODB-SERVER-8.0.gpg')
MONGODB_RUN_ROOT = pathlib.Path('/run/hostpanel-build')


def primary_fingerprints(colons: str) -> list[str]:
    fingerprints: list[str] = []
    awaiting = False
    for line in colons.splitlines():
        fields = line.split(':')
        record = fields[0] if fields else ''
        if record == 'pub':
            awaiting = True
        elif record in {'sub', 'ssb'}:
            awaiting = False
        elif record == 'fpr' and awaiting:
            if len(fields) <= 9 or not fields[9]:
                raise BuildError('MongoDB repository key has a malformed primary fingerprint')
            fingerprints.append(fields[9].upper())
            awaiting = False
    return fingerprints


def verify_mongodb_key(path: pathlib.Path) -> None:
    completed = run_command(
        ['gpg', '--batch', '--show-keys', '--with-colons', str(path)],
        check=False, capture=True,
    )
    if completed.returncode != 0:
        raise BuildError('MongoDB repository signing key could not be parsed')
    fingerprints = primary_fingerprints(completed.stdout)
    if fingerprints != [base.MONGODB_KEY_FINGERPRINT]:
        raise BuildError(
            'MongoDB repository key must contain exactly the pinned primary key'
        )


def ensure_private_runtime_dir(path: pathlib.Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'cannot create MongoDB runtime directory: {path}') from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BuildError(f'unsafe MongoDB runtime directory: {path}')
    path.chmod(0o700)


def configure_mongodb_repository(platform: Platform, log_path: pathlib.Path) -> None:
    base.require_root()
    family, release = base.mongodb_supported(platform)
    for command in ('curl', 'gpg'):
        if shutil.which(command) is None:
            raise BuildError(f'{command} is required to configure the MongoDB repository')
    ensure_private_runtime_dir(MONGODB_RUN_ROOT)
    with tempfile.TemporaryDirectory(prefix='mongodb.', dir=MONGODB_RUN_ROOT) as directory:
        root = pathlib.Path(directory)
        key = root / 'server-8.0.asc'
        keyring = root / 'server-8.0.gpg'
        run_command([
            'curl', '-q', '--fail', '--silent', '--show-error', '--location',
            '--proto', '=https', '--tlsv1.2', '--output', str(key), base.MONGODB_KEY_URL,
        ], log_path=log_path)
        verify_mongodb_key(key)
        run_command([
            'gpg', '--batch', '--yes', '--dearmor', '--output', str(keyring), str(key)
        ], log_path=log_path)
        keyring_bytes = keyring.read_bytes()
        if family in {'ubuntu', 'debian'}:
            base.write_atomic_bytes(base.MONGODB_APT_KEY, keyring_bytes, 0o644)
        else:
            base.write_atomic_bytes(MONGODB_RPM_KEY, keyring_bytes, 0o644)

    if family == 'ubuntu':
        base.MONGODB_RPM_REPO.unlink(missing_ok=True)
        MONGODB_RPM_KEY.unlink(missing_ok=True)
        line = (
            f'deb [ arch=amd64 signed-by={base.MONGODB_APT_KEY} ] '
            f'https://repo.mongodb.org/apt/ubuntu {release}/mongodb-org/8.0 multiverse\n'
        )
        base.write_atomic_text(base.MONGODB_APT_LIST, line)
    elif family == 'debian':
        base.MONGODB_RPM_REPO.unlink(missing_ok=True)
        MONGODB_RPM_KEY.unlink(missing_ok=True)
        line = (
            f'deb [ arch=amd64 signed-by={base.MONGODB_APT_KEY} ] '
            f'https://repo.mongodb.org/apt/debian {release}/mongodb-org/8.0 main\n'
        )
        base.write_atomic_text(base.MONGODB_APT_LIST, line)
    else:
        base.MONGODB_APT_KEY.unlink(missing_ok=True)
        base.MONGODB_APT_LIST.unlink(missing_ok=True)
        repo = (
            '[mongodb-org-8.0]\n'
            'name=MongoDB Repository\n'
            f'baseurl=https://repo.mongodb.org/yum/redhat/{release}/mongodb-org/8.0/$basearch/\n'
            'gpgcheck=1\n'
            'enabled=1\n'
            f'gpgkey=file://{MONGODB_RPM_KEY}\n'
        )
        base.write_atomic_text(base.MONGODB_RPM_REPO, repo)


def install() -> None:
    base.verify_mongodb_key = verify_mongodb_key
    base.configure_mongodb_repository = configure_mongodb_repository
