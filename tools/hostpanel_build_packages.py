"""Package manager, version and logging helpers for hostpanel-build."""
from __future__ import annotations

import os
import pathlib
import shlex
import stat
import subprocess
from typing import Iterable, Sequence

from hostpanel_build_config import (
    BuildError, PackageState, Platform, component_packages, owner_ids,
)


def open_private_log(path: pathlib.Path):
    uid, gid = owner_ids()
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != uid
        or parent.st_gid != gid
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe log directory: {path.parent}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BuildError(f'could not open build log safely: {exc}') from exc
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        os.close(fd)
        raise BuildError(f'unsafe build log: {path}')
    return os.fdopen(fd, 'a', encoding='utf-8')


def run_command(
    command: Sequence[str], *, check: bool = True, capture: bool = False,
    log_path: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in ('PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        environment.pop(name, None)
    environment['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    if capture:
        completed = subprocess.run(
            list(command), env=environment, text=True, capture_output=True, check=False
        )
    elif log_path is not None:
        with open_private_log(log_path) as log:
            log.write(f'\n$ {shlex.join(command)}\n')
            log.flush()
            completed = subprocess.run(
                list(command), env=environment, text=True,
                stdout=log, stderr=subprocess.STDOUT, check=False,
            )
    else:
        completed = subprocess.run(list(command), env=environment, text=True, check=False)
    if check and completed.returncode != 0:
        raise BuildError(f"command failed ({completed.returncode}): {shlex.join(command)}")
    return completed


def installed_version(package: str, platform: Platform) -> str | None:
    if platform.family == 'debian':
        result = run_command(
            ['dpkg-query', '-W', '-f=${Status}\t${Version}\n', package],
            check=False, capture=True,
        )
        prefix = 'install ok installed\t'
        return result.stdout[len(prefix):].strip() if result.returncode == 0 and result.stdout.startswith(prefix) else None
    result = run_command(
        ['rpm', '-q', '--qf', '%{VERSION}-%{RELEASE}\n', package],
        check=False, capture=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def candidate_version(package: str, platform: Platform) -> str | None:
    if platform.family == 'debian':
        result = run_command(['apt-cache', 'policy', package], check=False, capture=True)
        for line in result.stdout.splitlines():
            if line.strip().startswith('Candidate:'):
                value = line.split(':', 1)[1].strip()
                return None if value == '(none)' else value
        return None
    result = run_command(
        ['dnf', '-q', '--showduplicates', 'list', '--available', package],
        check=False, capture=True,
    )
    versions = [
        parts[1] for line in result.stdout.splitlines()
        if len(parts := line.split()) >= 2 and parts[0].split('.', 1)[0] == package
    ]
    return versions[-1] if versions else None


def package_states(
    components: Iterable[str], options: dict[str, str], platform: Platform
) -> list[PackageState]:
    states: list[PackageState] = []
    seen: set[str] = set()
    for component in components:
        for package in component_packages(component, options, platform):
            if package in seen:
                continue
            seen.add(package)
            states.append(PackageState(
                component, package, installed_version(package, platform),
                candidate_version(package, platform),
            ))
    return states


def refresh_packages(platform: Platform, log_path: pathlib.Path) -> None:
    if platform.family == 'debian':
        command = [
            'apt-get', '-o', 'DPkg::Lock::Timeout=120',
            '-o', 'Acquire::Retries=3', 'update', '-qq',
        ]
    else:
        command = ['dnf', '-q', '--setopt=timeout=60', '--setopt=retries=5', 'makecache']
    run_command(command, log_path=log_path)


def reinstall_packages(
    packages: Sequence[str], platform: Platform, log_path: pathlib.Path
) -> None:
    if not packages:
        return
    if platform.family == 'debian':
        run_command([
            'apt-get', '-o', 'DPkg::Lock::Timeout=120',
            '-o', 'Acquire::Retries=3', 'install', '--reinstall', '-y',
            '--no-install-recommends', *packages,
        ], log_path=log_path)
        return
    installed = [package for package in packages if installed_version(package, platform)]
    missing = [package for package in packages if package not in installed]
    common = ['dnf', '-y', '-q', '--setopt=timeout=60', '--setopt=retries=5']
    if installed:
        run_command([*common, 'reinstall', *installed], log_path=log_path)
    if missing:
        run_command([*common, 'install', *missing], log_path=log_path)


def check_system_updates(platform: Platform) -> int:
    if platform.family == 'debian':
        result = run_command(['apt-get', '-s', 'upgrade'], check=False, capture=True)
        if result.returncode != 0:
            raise BuildError('apt-get simulation failed')
        lines = [line for line in result.stdout.splitlines() if line.startswith('Inst ')]
    else:
        result = run_command(['dnf', '-q', 'check-update'], check=False, capture=True)
        if result.returncode not in {0, 100}:
            raise BuildError('dnf check-update failed')
        lines = [
            line for line in result.stdout.splitlines()
            if line and not line.startswith('Last metadata')
        ]
    if lines:
        print('\n'.join(lines))
        return 10
    print('System packages are current.')
    return 0


def apply_system_updates(platform: Platform, log_path: pathlib.Path) -> None:
    refresh_packages(platform, log_path)
    if platform.family == 'debian':
        command = [
            'apt-get', '-o', 'DPkg::Lock::Timeout=120',
            '-o', 'Acquire::Retries=3', 'upgrade', '-y',
        ]
    else:
        command = ['dnf', '-y', '-q', '--setopt=timeout=60', '--setopt=retries=5', 'upgrade']
    run_command(command, log_path=log_path)
