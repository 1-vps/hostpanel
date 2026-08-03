"""Runtime compatibility adapter for PowerDNS BIND-backend permissions."""
from __future__ import annotations

import os
import pathlib
import stat

import hostpanel_build_operations as operations
from hostpanel_build_config import BuildError, Platform


def trusted_root_directory(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe PowerDNS include directory: {path}')


def trusted_root_file(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe PowerDNS backend configuration: {path}')


def readable_backend_paths() -> tuple[pathlib.Path, pathlib.Path]:
    native = operations.native_powerdns_config()
    include_dir = operations.powerdns_include_dir(native)
    target = operations.select_powerdns_backend_config(
        native, include_dir, include_dir / operations.PDNS_DROPIN_NAME
    )
    trusted_root_directory(include_dir)
    trusted_root_file(target)
    return include_dir, target


def install() -> None:
    original = operations.configure_powerdns
    if getattr(original, '_hostpanel_readable_backend', False):
        return

    def configure(platform: Platform, log_path: pathlib.Path) -> None:
        original(platform, log_path)
        include_dir, target = readable_backend_paths()
        os.chown(include_dir, 0, 0)
        os.chmod(include_dir, 0o755)
        os.chown(target, 0, 0)
        os.chmod(target, 0o644)

    configure._hostpanel_readable_backend = True  # type: ignore[attr-defined]
    operations.configure_powerdns = configure
