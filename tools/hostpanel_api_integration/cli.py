from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Sequence

from hostpanel_api_http import Request


@dataclass(frozen=True, slots=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str


class ApiCli:
    """CLI parity adapter that dispatches through the exact same route table."""

    def __init__(self, application) -> None:
        if not callable(getattr(application, "dispatch", None)):
            raise TypeError("application must implement dispatch()")
        self.application = application

    def execute(self, argv: Sequence[str], *, stdin: str = "", now: int | None = None) -> CliResult:
        parser = argparse.ArgumentParser(prog="hostpanel-api", add_help=False)
        parser.add_argument("method")
        parser.add_argument("path")
        parser.add_argument("--query", default="")
        parser.add_argument("--token")
        parser.add_argument("--request-id")
        parser.add_argument("--idempotency-key")
        parser.add_argument("--source-ip", default="127.0.0.1")
        parser.add_argument("--body")
        parser.add_argument("--body-stdin", action="store_true")
        parser.add_argument("--pretty", action="store_true")
        try:
            args = parser.parse_args(list(argv))
        except SystemExit:
            return CliResult(64, "", "invalid command line\n")
        if args.body is not None and args.body_stdin:
            return CliResult(64, "", "choose one request-body source\n")
        body_text = stdin if args.body_stdin else args.body
        body = b""
        headers: list[tuple[str, str]] = []
        if args.token:
            headers.append(("Authorization", "Bearer " + args.token))
        if args.request_id:
            headers.append(("X-Request-Id", args.request_id))
        if args.idempotency_key:
            headers.append(("Idempotency-Key", args.idempotency_key))
        if body_text is not None:
            try:
                decoded = json.loads(body_text)
                body = json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            except (TypeError, ValueError, json.JSONDecodeError):
                return CliResult(64, "", "request body must be valid JSON\n")
            headers.append(("Content-Type", "application/json"))
        response = self.application.dispatch(
            Request(
                method=args.method,
                raw_path=args.path,
                query_string=args.query,
                headers=tuple(headers),
                body=body,
                source_ip=args.source_ip,
                scheme="https",
                host="localhost",
            ),
            now=now,
        )
        output = response.body.decode("utf-8") if response.body else ""
        if args.pretty and output:
            try:
                output = json.dumps(json.loads(output), indent=2, sort_keys=True) + "\n"
            except json.JSONDecodeError:
                output += "\n"
        elif output:
            output += "\n"
        return CliResult(0 if 200 <= response.status <= 299 else 1, output, "")
