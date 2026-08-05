from __future__ import annotations

import io
import secrets
from http import HTTPStatus
from typing import Any

from .model import ApiProblem, Request
from .routing import ApiApplication

MAX_WSGI_BODY_BYTES = 16 * 1024 * 1024


class WsgiAdapter:
    """WSGI callable only; it never opens a socket or starts a server."""

    def __init__(self, application: ApiApplication) -> None:
        if not isinstance(application, ApiApplication):
            raise TypeError("application must be ApiApplication")
        self.application = application

    def __call__(self, environ: dict[str, Any], start_response):
        try:
            request = self._request(environ)
            response = self.application.dispatch(request)
        except ApiProblem as problem:
            response = self.application.render_problem(
                problem,
                request_id="req_" + secrets.token_urlsafe(16),
            )
        phrase = HTTPStatus(response.status).phrase
        start_response(f"{response.status} {phrase}", list(response.headers))
        return [response.body]

    @staticmethod
    def _request(environ: dict[str, Any]) -> Request:
        method = environ.get("REQUEST_METHOD", "")
        raw_uri = environ.get("RAW_URI") or environ.get("REQUEST_URI")
        if isinstance(raw_uri, str):
            raw_path = raw_uri.split("?", 1)[0]
        else:
            raw_path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")
        headers: list[tuple[str, str]] = []
        for key, value in environ.items():
            if key.startswith("HTTP_") and isinstance(value, str):
                name = key[5:].replace("_", "-")
                headers.append((name, value))
        if isinstance(environ.get("CONTENT_TYPE"), str):
            headers.append(("Content-Type", environ["CONTENT_TYPE"]))
        if isinstance(environ.get("CONTENT_LENGTH"), str) and environ["CONTENT_LENGTH"]:
            headers.append(("Content-Length", environ["CONTENT_LENGTH"]))
        transfer_encoding = environ.get("HTTP_TRANSFER_ENCODING")
        if transfer_encoding:
            raise ApiProblem(
                400,
                "unsupported_transfer_encoding",
                "Transfer-Encoding is not accepted by this adapter.",
            )
        stream = environ.get("wsgi.input", io.BytesIO())
        raw_length = environ.get("CONTENT_LENGTH", "")
        if raw_length:
            try:
                length = int(raw_length)
            except (TypeError, ValueError) as exc:
                raise ApiProblem(400, "invalid_content_length", "Content-Length is invalid.") from exc
            if length < 0 or length > MAX_WSGI_BODY_BYTES:
                raise ApiProblem(413, "payload_too_large", "The request body is too large.")
            body = stream.read(length)
            if len(body) != length:
                raise ApiProblem(400, "incomplete_body", "The request body is incomplete.")
        else:
            body = stream.read(MAX_WSGI_BODY_BYTES + 1)
            if len(body) > MAX_WSGI_BODY_BYTES:
                raise ApiProblem(413, "payload_too_large", "The request body is too large.")
        host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "localhost"
        return Request(
            method=method,
            raw_path=raw_path,
            query_string=query,
            headers=tuple(headers),
            body=body,
            source_ip=environ.get("REMOTE_ADDR", "127.0.0.1"),
            scheme=environ.get("wsgi.url_scheme", "https"),
            host=host,
        )
