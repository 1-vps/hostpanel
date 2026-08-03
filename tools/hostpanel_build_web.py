#!/usr/bin/env python3
"""Apply one HostPanel webserver mode to every managed domain."""
from __future__ import annotations

import argparse
import pathlib
import sys

APP_ROOT = pathlib.Path('/opt/hostpanel/app')
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import store  # type: ignore  # noqa: E402
from modules import webserver  # type: ignore  # noqa: E402

MODE_MAP = {
    'nginx_apache': 'hybrid',
    'nginx': 'nginx',
    'apache': 'apache',
    'openlitespeed': 'openlitespeed',
}


def managed_domains() -> list[str]:
    with store.connect() as database:
        rows = database.execute(
            "SELECT name FROM resources WHERE kind='domain' ORDER BY name"
        ).fetchall()
    return [str(row['name']) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=tuple(MODE_MAP))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    target = MODE_MAP[args.mode]
    domains = managed_domains()
    if args.check:
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            print('\n'.join(mismatches))
            return 10
        print(f'All {len(domains)} managed domains use {args.mode}.')
        return 0
    admin = {'role': 'admin', 'user_id': 0, 'username': 'root'}
    changed = 0
    for domain in domains:
        result = webserver.set_mode(domain, target, admin)
        changed += int(bool(result.get('changed')))
    print(f'Applied {args.mode} to {len(domains)} domains ({changed} changed).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
