#!/usr/bin/env python3
"""Render the fail-closed Buildkite policy for HostPanel's QEMU repository token."""

from __future__ import annotations

import argparse
import uuid


def canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise SystemExit(f"{label} must be a UUID") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise SystemExit(f"{label} must use canonical UUID form")
    return canonical


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", choices=("qemu",))
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--qemu-queue-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pipeline_id = canonical_uuid(args.pipeline_id, "--pipeline-id")
    queue_id = canonical_uuid(args.qemu_queue_id, "--qemu-queue-id")
    print(f'- pipeline_id: "{pipeline_id}"')
    print('  build_source: "webhook"')
    print('  build_branch: "main"')
    print(f'  cluster_queue_id: "{queue_id}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
