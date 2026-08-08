#!/usr/bin/env python3
"""Capture, verify, and restore complete HostPanel web reconciliation state."""
from __future__ import annotations

import argparse
import base64
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import stat
import sys
from typing import Any

MAX_STATE_BYTES = 32 * 1024 * 1024
STATE_SCHEMA = 1
DEFAULT_MODE_FILE = pathlib.Path('/etc/hostpanel/webserver-mode')


def _load_web_module():
    source = pathlib.Path(__file__).with_name('hostpanel_build_web.py')
    if not source.is_file():
        source = pathlib.Path(__file__).with_name('hostpanel-build-web')
    loader = importlib.machinery.SourceFileLoader(
        '_hostpanel_build_web_state_runtime', str(source)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise ImportError(f'cannot load HostPanel web helper: {source}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


web = _load_web_module()
BuildError = web.BuildError


def _encode_xattrs(values: dict[str, bytes]) -> dict[str, str]:
    return {
        name: base64.b64encode(value).decode('ascii')
        for name, value in sorted(values.items())
    }


def _decode_xattrs(value: object) -> dict[str, bytes]:
    if not isinstance(value, dict) or len(value) > 128:
        raise BuildError('invalid web transaction extended attributes')
    result: dict[str, bytes] = {}
    for name, encoded in value.items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or not isinstance(encoded, str)
        ):
            raise BuildError('invalid web transaction extended attribute')
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise BuildError('invalid web transaction extended attribute encoding') from exc
        if len(decoded) > 1024 * 1024:
            raise BuildError('web transaction extended attribute is too large')
        result[name] = decoded
    return result


def _encode_snapshot(snapshot: tuple[object, ...]) -> dict[str, object]:
    kind = snapshot[0]
    if kind == 'missing':
        return {'kind': 'missing'}
    if kind == 'symlink':
        return {'kind': 'symlink', 'target': str(snapshot[1])}
    if kind == 'file':
        return {
            'kind': 'file',
            'text': str(snapshot[1]),
            'mode': int(snapshot[2]),
            'uid': int(snapshot[3]),
            'gid': int(snapshot[4]),
            'xattrs': _encode_xattrs(dict(snapshot[5])),
        }
    if kind == 'directory':
        return {
            'kind': 'directory',
            'mode': int(snapshot[1]),
            'uid': int(snapshot[2]),
            'gid': int(snapshot[3]),
            'xattrs': _encode_xattrs(dict(snapshot[4])),
        }
    raise BuildError(f'unsupported web transaction snapshot kind: {kind}')


def _exact_keys(value: dict[str, object], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise BuildError(f'invalid {label} shape')


def _decode_identity(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BuildError(f'invalid {label}')
    return value


def _decode_mode(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > 0o7777
    ):
        raise BuildError('invalid web transaction mode')
    return value


def _decode_snapshot(value: object) -> tuple[object, ...]:
    if not isinstance(value, dict):
        raise BuildError('invalid web transaction snapshot')
    kind = value.get('kind')
    if kind == 'missing':
        _exact_keys(value, {'kind'}, 'missing snapshot')
        return ('missing',)
    if kind == 'symlink':
        _exact_keys(value, {'kind', 'target'}, 'symlink snapshot')
        target = value['target']
        if not isinstance(target, str) or not target or len(target) > 4096:
            raise BuildError('invalid web transaction symlink target')
        return ('symlink', target)
    if kind == 'file':
        _exact_keys(
            value,
            {'kind', 'text', 'mode', 'uid', 'gid', 'xattrs'},
            'file snapshot',
        )
        text = value['text']
        if not isinstance(text, str):
            raise BuildError('invalid web transaction file text')
        return (
            'file', text, _decode_mode(value['mode']),
            _decode_identity(value['uid'], 'web transaction uid'),
            _decode_identity(value['gid'], 'web transaction gid'),
            _decode_xattrs(value['xattrs']),
        )
    if kind == 'directory':
        _exact_keys(
            value,
            {'kind', 'mode', 'uid', 'gid', 'xattrs'},
            'directory snapshot',
        )
        return (
            'directory', _decode_mode(value['mode']),
            _decode_identity(value['uid'], 'web transaction uid'),
            _decode_identity(value['gid'], 'web transaction gid'),
            _decode_xattrs(value['xattrs']),
        )
    raise BuildError('invalid web transaction snapshot kind')


def _canonical_path(value: object) -> pathlib.Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise BuildError('invalid web transaction path')
    path = pathlib.Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise BuildError(f'non-canonical web transaction path: {path}')
    return path


def _state_payload(
    mode_file: pathlib.Path,
) -> tuple[dict[str, object], list[tuple[str, pathlib.Path, tuple[object, ...]]]]:
    web._trusted_directory_chain(mode_file.parent)
    snapshots = list(web._preparation_snapshots())
    snapshots.append(('path', mode_file, web._snapshot_path(mode_file)))
    domain_modes = {
        domain: web.webserver.mode_of(domain)
        for domain in web.managed_domains()
    }
    allowed_modes = set(web.MODE_MAP.values())
    if any(mode not in allowed_modes for mode in domain_modes.values()):
        raise BuildError('managed domain has an unsupported webserver mode')
    payload = {
        'schema': STATE_SCHEMA,
        'mode_file': str(mode_file),
        'domains': domain_modes,
        'snapshots': [
            {
                'entry_kind': kind,
                'path': str(path),
                'snapshot': _encode_snapshot(snapshot),
            }
            for kind, path, snapshot in snapshots
        ],
    }
    return payload, snapshots


def _validate_state_shape(
    value: object,
) -> tuple[
    pathlib.Path,
    dict[str, str],
    list[tuple[str, pathlib.Path, tuple[object, ...]]],
]:
    if not isinstance(value, dict):
        raise BuildError('invalid web transaction state')
    _exact_keys(
        value,
        {'schema', 'mode_file', 'domains', 'snapshots'},
        'web transaction state',
    )
    if value['schema'] != STATE_SCHEMA:
        raise BuildError('unsupported web transaction state schema')
    mode_file = _canonical_path(value['mode_file'])

    raw_domains = value['domains']
    if not isinstance(raw_domains, dict) or len(raw_domains) > 100000:
        raise BuildError('invalid web transaction domain state')
    allowed_modes = set(web.MODE_MAP.values())
    domains: dict[str, str] = {}
    for name, mode in raw_domains.items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 253
            or not isinstance(mode, str)
            or mode not in allowed_modes
        ):
            raise BuildError('invalid web transaction domain entry')
        domains[name] = mode

    raw_snapshots = value['snapshots']
    if not isinstance(raw_snapshots, list) or not raw_snapshots or len(raw_snapshots) > 128:
        raise BuildError('invalid web transaction snapshot list')
    snapshots: list[tuple[str, pathlib.Path, tuple[object, ...]]] = []
    seen_paths: set[pathlib.Path] = set()
    for raw in raw_snapshots:
        if not isinstance(raw, dict):
            raise BuildError('invalid web transaction snapshot entry')
        _exact_keys(
            raw, {'entry_kind', 'path', 'snapshot'},
            'web transaction snapshot entry',
        )
        entry_kind = raw['entry_kind']
        if entry_kind not in {'path', 'directory'}:
            raise BuildError('invalid web transaction entry kind')
        path = _canonical_path(raw['path'])
        if path in seen_paths:
            raise BuildError(f'duplicate web transaction path: {path}')
        seen_paths.add(path)
        snapshot = _decode_snapshot(raw['snapshot'])
        if entry_kind == 'directory' and snapshot[0] not in {'missing', 'directory'}:
            raise BuildError('directory transaction entry has a non-directory snapshot')
        if entry_kind == 'path' and snapshot[0] == 'directory':
            raise BuildError('path transaction entry has a directory snapshot')
        snapshots.append((str(entry_kind), path, snapshot))

    mode_matches = [
        item for item in snapshots
        if item[0] == 'path' and item[1] == mode_file
    ]
    if len(mode_matches) != 1:
        raise BuildError('web transaction state does not contain exactly one mode file')
    return mode_file, domains, snapshots


def _write_state(path: pathlib.Path, payload: dict[str, object]) -> None:
    if os.path.lexists(path):
        raise BuildError(f'web transaction state already exists: {path}')
    web._trusted_directory_chain(path.parent)
    text = json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n'
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_STATE_BYTES:
        raise BuildError('web transaction state exceeds the size limit')
    web._replace_atomic(path, text, 0o600, os.geteuid(), os.getegid(), None)


def _read_state_bytes(path: pathlib.Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BuildError(f'cannot open web transaction state: {path}') from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > MAX_STATE_BYTES
        ):
            raise BuildError(f'unsafe web transaction state: {path}')
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise BuildError('web transaction state ended unexpectedly')
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BuildError('web transaction state grew while being read')
        after = os.fstat(descriptor)
        path_state = path.lstat()
        identity_before = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        )
        identity_path = (
            path_state.st_dev, path_state.st_ino,
            path_state.st_size, path_state.st_mtime_ns,
        )
        if identity_before != identity_after or identity_after != identity_path:
            raise BuildError('web transaction state changed while being read')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def load_state(
    path: pathlib.Path,
) -> tuple[
    pathlib.Path,
    dict[str, str],
    list[tuple[str, pathlib.Path, tuple[object, ...]]],
]:
    try:
        value: Any = json.loads(_read_state_bytes(path).decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError('invalid web transaction state JSON') from exc
    return _validate_state_shape(value)


def capture_state(path: pathlib.Path, mode_file: pathlib.Path) -> None:
    payload, _snapshots = _state_payload(mode_file)
    _write_state(path, payload)


def _current_domain_modes() -> dict[str, str]:
    return {
        domain: web.webserver.mode_of(domain)
        for domain in web.managed_domains()
    }


def verify_state(path: pathlib.Path) -> None:
    mode_file, domains, snapshots = load_state(path)
    current = _current_domain_modes()
    if current != domains:
        raise BuildError('managed domain webserver state changed during build preparation')
    mode_snapshot = next(
        snapshot for kind, item_path, snapshot in snapshots
        if kind == 'path' and item_path == mode_file
    )
    if web._snapshot_path(mode_file) != mode_snapshot:
        raise BuildError('applied webserver mode changed during build preparation')


def restore_state(path: pathlib.Path) -> None:
    _mode_file, domains, snapshots = load_state(path)
    errors: list[str] = []
    current_names = set(web.managed_domains())
    captured_names = set(domains)
    missing = sorted(captured_names - current_names)
    extra = sorted(current_names - captured_names)
    if missing:
        errors.append('managed domains disappeared: ' + ', '.join(missing[:10]))
    if extra:
        errors.append('new managed domains appeared: ' + ', '.join(extra[:10]))

    admin: dict[str, object] = {'role': 'admin', 'user_id': 0, 'username': 'root'}
    for domain in sorted(captured_names & current_names, reverse=True):
        try:
            if web.webserver.mode_of(domain) != domains[domain]:
                web.webserver.set_mode(domain, domains[domain], admin)
            restored = web.webserver.mode_of(domain)
            if restored != domains[domain]:
                raise BuildError(
                    f'{domain} restored to {restored}; expected {domains[domain]}'
                )
        except Exception as exc:
            errors.append(f'{domain}: {exc}')

    errors.extend(web._restore_preparation(snapshots))
    if errors:
        raise BuildError('web transaction rollback failed: ' + '; '.join(errors))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--capture', type=pathlib.Path)
    actions.add_argument('--verify', type=pathlib.Path)
    actions.add_argument('--restore', type=pathlib.Path)
    parser.add_argument('--mode-file', type=pathlib.Path, default=DEFAULT_MODE_FILE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        raise BuildError('web transaction state helper must run as root')
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.capture is not None:
        capture_state(args.capture, args.mode_file)
        print(f'Captured web transaction state: {args.capture}')
    elif args.verify is not None:
        verify_state(args.verify)
        print(f'Verified web transaction state: {args.verify}')
    else:
        restore_state(args.restore)
        print(f'Restored web transaction state: {args.restore}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BuildError, OSError) as exc:
        print(f'hostpanel-build-web-state failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
