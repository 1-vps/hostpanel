#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_http import ApiApplication, generate_openapi
from hostpanel_api_http.jsonio import canonical_json_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the HostPanel API v1 OpenAPI contract")
    parser.add_argument("--output", type=Path, help="Write to a file instead of stdout")
    arguments = parser.parse_args(argv)
    payload = canonical_json_bytes(generate_openapi(ApiApplication().routes)) + b"\n"
    if arguments.output is None:
        sys.stdout.buffer.write(payload)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
