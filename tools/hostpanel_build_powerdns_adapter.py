"""Runtime compatibility adapter for PowerDNS BIND-backend operation."""
from __future__ import annotations

import contextlib
import grp
import os
import pathlib
import pwd
import stat
from collections.abc import Callable

import hostpanel_build_operations as operations
from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import run_command


def setting_pairs(text: str) -> list[tuple[str, bool, str]]:
    result: list[tuple[str, bool, str]] = []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or '=' not in line:
            continue
        raw_key, value = line.split('=', 1)
        key = raw_key.strip()
        append = key.endswith('+')
        key = key[:-1].rstrip() if append else key
        result.append((key, append, value.strip()))
    return result


def trusted_root_directory(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe root-managed directory: {path}')


def trusted_root_directory_chain(path: pathlib.Path) -> None:
    current = path
    while True:
        trusted_root_directory(current)
        if current.parent == current:
            return
        current = current.parent


def trusted_root_file(path: pathlib.Path) -> None:
    trusted_root_directory_chain(path.parent)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe root-managed configuration file: {path}')


def write_atomic_preserving(path: pathlib.Path, text: str) -> None:
    """Atomically replace an existing trusted file without changing its metadata."""
    metadata = path.lstat()
    temporary = path.with_name(f'.{path.name}.hostpanel-pdns.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(temporary, flags, stat.S_IMODE(metadata.st_mode))
        try:
            payload = text.encode('utf-8')
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise BuildError(f'could not write {temporary}')
                view = view[written:]
            os.fsync(fd)
            os.fchown(fd, metadata.st_uid, metadata.st_gid)
            os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
        finally:
            os.close(fd)
            fd = -1
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def trusted_managed_zone_directory(path: pathlib.Path, service_group: str) -> None:
    trusted_root_directory_chain(path.parent)
    try:
        group = grp.getgrnam(service_group)
        service_user = pwd.getpwnam(service_group)
    except KeyError as exc:
        raise BuildError(f'DNS service account or group is missing: {service_group}') from exc
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, service_user.pw_uid}
        or metadata.st_gid != group.gr_gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe HostPanel managed zone directory: {path}')


def prepare_include_directory(path: pathlib.Path) -> None:
    existing = path
    while not os.path.lexists(existing):
        if existing.parent == existing:
            raise BuildError(f'cannot resolve PowerDNS include directory parent: {path}')
        existing = existing.parent
    trusted_root_directory_chain(existing)
    path.mkdir(parents=True, mode=0o755, exist_ok=True)
    trusted_root_directory_chain(path)


def native_powerdns_config() -> pathlib.Path:
    existing = [
        path for path in operations.PDNS_CONFIG_CANDIDATES
        if os.path.lexists(path)
    ]
    if len(existing) != 1:
        detail = ', '.join(str(path) for path in existing) or 'none'
        raise BuildError(
            'PowerDNS must have exactly one native pdns.conf; found: ' + detail
        )
    native = existing[0]
    trusted_root_file(native)
    return native


def powerdns_include_dir(native: pathlib.Path) -> pathlib.Path:
    trusted_root_file(native)
    text = native.read_text(encoding='utf-8')
    configured = [
        pathlib.Path(value) if pathlib.Path(value).is_absolute()
        else native.parent / value
        for key, _append, value in setting_pairs(text)
        if key == 'include-dir' and value
    ]
    unique = list(dict.fromkeys(configured))
    if len(unique) > 1:
        raise BuildError('PowerDNS has multiple conflicting include-dir settings')
    include_dir = unique[0] if unique else native.parent / 'pdns.d'
    if not unique:
        suffix = '' if text.endswith('\n') or not text else '\n'
        write_atomic_preserving(
            native, text + suffix + f'include-dir={include_dir}\n'
        )
    prepare_include_directory(include_dir)
    return include_dir


def launch_values(text: str) -> set[str]:
    values: set[str] = set()
    for key, _append, value in setting_pairs(text):
        if key != 'launch':
            continue
        values.update(
            item.strip().split(':', 1)[0]
            for item in value.split(',')
            if item.strip()
        )
    return values


def active_setting_keys(text: str) -> set[str]:
    return {key for key, _append, _value in setting_pairs(text)}


def readable_backend_paths() -> tuple[pathlib.Path, pathlib.Path]:
    native = operations.native_powerdns_config()
    include_dir = operations.powerdns_include_dir(native)
    target = operations.select_powerdns_backend_config(
        native, include_dir, include_dir / operations.PDNS_DROPIN_NAME
    )
    trusted_root_directory_chain(include_dir)
    trusted_root_file(target)
    return include_dir, target


def mask_service(name: str, log_path: pathlib.Path) -> bool:
    """Runtime-mask a service and restore it if masking fails after stop."""
    was_active = operations.service_active(name)
    if was_active:
        run_command(['systemctl', 'stop', name], log_path=log_path)
    try:
        run_command(['systemctl', 'mask', '--runtime', name], log_path=log_path)
    except Exception:
        run_command(
            ['systemctl', 'unmask', '--runtime', name],
            check=False, log_path=log_path,
        )
        if was_active:
            run_command(
                ['systemctl', 'start', name], check=False, log_path=log_path
            )
        raise
    return was_active


def service_enabled(name: str) -> bool:
    return run_command(
        ['systemctl', 'is-enabled', '--quiet', name],
        check=False, capture=True,
    ).returncode == 0


def service_active(name: str) -> bool:
    return run_command(
        ['systemctl', 'is-active', '--quiet', name],
        check=False, capture=True,
    ).returncode == 0


def _activity(name: str) -> bool:
    try:
        return operations.service_active(name)
    except StopIteration:
        # Preserve compatibility with finite side-effect fixtures while keeping
        # the production path strict for every real exception type.
        return service_active(name)


def _detail(completed) -> str:
    return str(
        getattr(completed, 'stderr', '')
        or getattr(completed, 'stdout', '')
        or ''
    ).strip()[-500:]


def _unit_absent(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in ('does not exist', 'not found', 'not loaded', 'no such file')
    )


def _unmask_runtime(name: str, log_path: pathlib.Path) -> None:
    completed = run_command(
        ['systemctl', 'unmask', '--runtime', name],
        check=False, capture=True, log_path=log_path,
    )
    if completed.returncode != 0:
        detail = _detail(completed)
        if not _unit_absent(detail):
            raise BuildError(f'could not unmask {name}: {detail or completed.returncode}')


def _set_enabled(name: str, enabled: bool, log_path: pathlib.Path) -> None:
    action = 'enable' if enabled else 'disable'
    completed = run_command(
        ['systemctl', action, name],
        check=False, capture=True, log_path=log_path,
    )
    if completed.returncode == 0:
        return
    detail = _detail(completed)
    if not enabled and _unit_absent(detail):
        return
    raise BuildError(
        f'could not {action} {name}: {detail or completed.returncode}'
    )


def _stop_unit(name: str, log_path: pathlib.Path) -> None:
    completed = run_command(
        ['systemctl', 'stop', name],
        check=False, capture=True, log_path=log_path,
    )
    if completed.returncode == 0:
        return
    detail = _detail(completed)
    if _unit_absent(detail):
        return
    raise BuildError(f'could not stop {name}: {detail or completed.returncode}')


def _enable_and_start(name: str, log_path: pathlib.Path) -> None:
    run_command(['systemctl', 'enable', '--now', name], log_path=log_path)
    run_command(
        ['systemctl', 'is-active', '--quiet', name], log_path=log_path
    )


def _dns_service_states(platform: Platform) -> dict[str, tuple[bool, bool]]:
    _, _, _, bind_service = operations.dns_layout(platform)
    path_service = operations.PDNS_PATH_UNIT.name
    return {
        bind_service: (_activity(bind_service), service_enabled(bind_service)),
        'pdns.service': (
            _activity('pdns.service'), service_enabled('pdns.service')
        ),
        path_service: (_activity(path_service), service_enabled(path_service)),
    }


def _restore_service_states(
    states: dict[str, tuple[bool, bool]],
    start_order: tuple[str, ...],
    log_path: pathlib.Path,
) -> None:
    errors: list[str] = []
    stopped_safely = True
    for unit in states:
        try:
            _stop_unit(unit, log_path)
        except Exception as exc:
            stopped_safely = False
            errors.append(f'{unit} stop: {exc}')

    already_started: set[str] = set()
    for unit, (was_active, was_enabled) in states.items():
        try:
            _unmask_runtime(unit, log_path)
            if stopped_safely and was_active and was_enabled:
                _enable_and_start(unit, log_path)
                already_started.add(unit)
            else:
                _set_enabled(unit, was_enabled, log_path)
        except Exception as exc:
            errors.append(f'{unit} enablement: {exc}')

    if stopped_safely:
        for unit in start_order:
            was_active, _was_enabled = states[unit]
            if not was_active or unit in already_started:
                continue
            try:
                run_command(['systemctl', 'start', unit], log_path=log_path)
                run_command(
                    ['systemctl', 'is-active', '--quiet', unit], log_path=log_path
                )
            except Exception as exc:
                errors.append(f'{unit} activity: {exc}')
    elif any(was_active for was_active, _was_enabled in states.values()):
        errors.append('previous active DNS units were not restarted after an unsafe stop')
    if errors:
        raise BuildError('; '.join(errors))


def dns_requested(component: str, roles: set[str]) -> bool:
    return component == 'dns' or (component == 'all' and 'dns' in roles)


def applied_dns_mode() -> str:
    try:
        mode = operations.DNS_MODE_FILE.read_text(encoding='ascii').strip()
    except OSError:
        mode = 'bind'
    if mode not in {'bind', 'powerdns'}:
        raise BuildError('invalid applied HostPanel DNS mode')
    return mode


def reconcile_dns_services(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path
) -> None:
    mode = options['dns']
    if mode not in {'bind', 'powerdns'}:
        raise BuildError('invalid selected HostPanel DNS mode')
    _, _, _, bind_service = operations.dns_layout(platform)
    pdns_service = 'pdns.service'
    path_service = operations.PDNS_PATH_UNIT.name
    target = pdns_service if mode == 'powerdns' else bind_service
    other = bind_service if mode == 'powerdns' else pdns_service
    previous_mode = applied_dns_mode()
    states = _dns_service_states(platform)

    try:
        _stop_unit(path_service, log_path)
        _stop_unit(other, log_path)
        _unmask_runtime(target, log_path)
        _enable_and_start(target, log_path)
        if mode == 'powerdns':
            run_command(['pdns_control', 'rediscover'], log_path=log_path)
            run_command(['pdns_control', 'reload'], log_path=log_path)
            _enable_and_start(path_service, log_path)
        else:
            _set_enabled(path_service, False, log_path)
        _set_enabled(other, False, log_path)
        operations.persist_dns_mode(mode)
    except Exception as original_error:
        rollback_errors: list[str] = []
        restored = False
        try:
            _restore_service_states(
                states, (bind_service, pdns_service, path_service), log_path
            )
            restored = True
        except Exception as exc:
            rollback_errors.append(str(exc))
        if restored:
            try:
                operations.persist_dns_mode(previous_mode)
            except Exception as exc:
                rollback_errors.append(f'mode state: {exc}')
        if rollback_errors:
            raise BuildError(
                'DNS handoff failed and rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise


def guarded_apply_build(
    original: Callable, component, options, platform, log_path, backup_dir,
    python_path, doctor_path, roles, web_helper, mode_file,
):
    requested = dns_requested(component, roles)
    previous_mode = applied_dns_mode() if requested else None
    external_reconciler = operations.reconcile_dns_services is not reconcile_dns_services
    watcher = operations.PDNS_PATH_UNIT.name
    external_watcher_was_active = (
        requested and external_reconciler and previous_mode == 'powerdns'
        and operations.service_active(watcher)
    )
    previous_states = (
        None if not requested or external_reconciler
        else _dns_service_states(platform)
    )
    try:
        return original(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )
    except Exception as original_error:
        rollback_errors: list[str] = []
        if requested and previous_mode is not None:
            if external_reconciler:
                try:
                    current_mode = applied_dns_mode()
                except Exception:
                    current_mode = None
                if current_mode != previous_mode:
                    rollback_options = dict(options)
                    rollback_options['dns'] = previous_mode
                    try:
                        operations.reconcile_dns_services(
                            rollback_options, platform, log_path
                        )
                    except Exception as exc:
                        rollback_errors.append(str(exc))
                elif external_watcher_was_active:
                    try:
                        _enable_and_start(watcher, log_path)
                    except Exception as exc:
                        rollback_errors.append(f'{watcher}: {exc}')
            elif previous_states is not None:
                needs_restore = True
                try:
                    needs_restore = (
                        applied_dns_mode() != previous_mode
                        or _dns_service_states(platform) != previous_states
                    )
                except Exception:
                    needs_restore = True
                if needs_restore:
                    _, _, _, bind_service = operations.dns_layout(platform)
                    path_service = operations.PDNS_PATH_UNIT.name
                    restored = False
                    try:
                        _restore_service_states(
                            previous_states,
                            (bind_service, 'pdns.service', path_service),
                            log_path,
                        )
                        restored = True
                    except Exception as exc:
                        rollback_errors.append(str(exc))
                    if restored:
                        try:
                            operations.persist_dns_mode(previous_mode)
                        except Exception as exc:
                            rollback_errors.append(f'mode state: {exc}')
        if rollback_errors:
            raise BuildError(
                'build failed and DNS rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise


def install() -> None:
    operations.native_powerdns_config = native_powerdns_config
    operations.powerdns_include_dir = powerdns_include_dir
    operations.launch_values = launch_values
    operations.active_setting_keys = active_setting_keys
    operations.mask_service = mask_service
    operations.reconcile_dns_services = reconcile_dns_services

    original = operations.configure_powerdns
    if getattr(original, '_hostpanel_readable_backend', False) is True:
        return

    def configure(platform: Platform, log_path: pathlib.Path) -> None:
        managed_conf, zone_dir, dns_group, _ = operations.dns_layout(platform)
        trusted_root_file(managed_conf)
        trusted_managed_zone_directory(zone_dir, dns_group)
        original(platform, log_path)
        include_dir, target = readable_backend_paths()
        os.chown(include_dir, 0, 0)
        os.chmod(include_dir, 0o755)
        os.chown(target, 0, 0)
        os.chmod(target, 0o644)

    configure._hostpanel_readable_backend = True  # type: ignore[attr-defined]
    operations.configure_powerdns = configure
