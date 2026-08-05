from __future__ import annotations

import io
from http import HTTPStatus

from tests.http_test_support import *  # noqa: F403


class HostpanelApiHttpWsgiTests(unittest.TestCase):  # noqa: F405
    def invoke(self, adapter, environ):
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = dict(response_headers)

        chunks = adapter(environ, start_response)
        return captured, b"".join(chunks)

    def base_environ(self, **overrides):
        values = {
            "REQUEST_METHOD": "GET",
            "RAW_URI": "/api/v1/health",
            "PATH_INFO": "/api/v1/health",
            "QUERY_STRING": "",
            "REMOTE_ADDR": "192.0.2.1",
            "wsgi.url_scheme": "https",
            "SERVER_NAME": "panel.example",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        values.update(overrides)
        return values

    def test_wsgi_adapter_dispatches_without_starting_a_server(self) -> None:
        adapter = WsgiAdapter(ApiApplication())  # noqa: F405
        captured, payload = self.invoke(adapter, self.base_environ())
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(json.loads(payload), {"api_version": "v1", "status": "ok"})
        self.assertIn("X-Request-Id", captured["headers"])

    def test_raw_uri_is_used_to_reject_encoded_path_aliases(self) -> None:
        adapter = WsgiAdapter(ApiApplication())  # noqa: F405
        captured, payload = self.invoke(
            adapter,
            self.base_environ(
                RAW_URI="/api/v1%2fhealth",
                PATH_INFO="/api/v1/health",
            ),
        )
        self.assertEqual(captured["status"], "400 Bad Request")
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_path")

    def test_remote_address_is_authoritative_and_forwarded_header_is_not_trusted(self) -> None:
        seen = []

        def authenticate(raw_token, source_ip, now):
            seen.append(source_ip)
            return PrincipalContext("service-a", "service_account", "token-a")  # noqa: F405

        controller = CallbackAccessController(  # noqa: F405
            authenticate=authenticate,
            authorize_target=lambda principal, required_scope, target, source_ip, now: None,
        )
        app = ApiApplication(access_controller=controller)  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/sites/{site_id}",
                operation_id="getSite",
                summary="Read site",
                required_scope="sites:read",
                resolve_target=target_resolver,  # noqa: F405
                handler=lambda context: {"ok": True},
            )
        )
        adapter = WsgiAdapter(app)  # noqa: F405
        captured, payload = self.invoke(
            adapter,
            self.base_environ(
                RAW_URI="/api/v1/sites/example.com",
                PATH_INFO="/api/v1/sites/example.com",
                REMOTE_ADDR="192.0.2.10",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",  # noqa: F405
                HTTP_X_FORWARDED_FOR="203.0.113.99",
            ),
        )
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(seen, ["192.0.2.10"])

    def test_invalid_incomplete_and_oversized_content_length_fail_closed(self) -> None:
        adapter = WsgiAdapter(ApiApplication())  # noqa: F405
        cases = (
            ("invalid", b"", "400 Bad Request", "invalid_content_length"),
            ("2", b"x", "400 Bad Request", "incomplete_body"),
            (str(16 * 1024 * 1024 + 1), b"", f"413 {HTTPStatus(413).phrase}", "payload_too_large"),
        )
        for length, payload, status, code in cases:
            with self.subTest(length=length):
                captured, response_body = self.invoke(
                    adapter,
                    self.base_environ(
                        REQUEST_METHOD="POST",
                        CONTENT_LENGTH=length,
                        **{"wsgi.input": io.BytesIO(payload)},
                    ),
                )
                self.assertEqual(captured["status"], status)
                self.assertEqual(json.loads(response_body)["error"]["code"], code)

    def test_transfer_encoding_is_rejected_before_body_read(self) -> None:
        adapter = WsgiAdapter(ApiApplication())  # noqa: F405
        captured, payload = self.invoke(
            adapter,
            self.base_environ(HTTP_TRANSFER_ENCODING="chunked"),
        )
        self.assertEqual(captured["status"], "400 Bad Request")
        self.assertEqual(
            json.loads(payload)["error"]["code"],
            "unsupported_transfer_encoding",
        )

    def test_wsgi_head_response_has_no_body(self) -> None:
        adapter = WsgiAdapter(ApiApplication())  # noqa: F405
        captured, payload = self.invoke(
            adapter,
            self.base_environ(REQUEST_METHOD="HEAD"),
        )
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(payload, b"")
        self.assertEqual(captured["headers"]["Content-Length"], "34")

    def test_transport_package_contains_no_listener_or_socket_startup(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tools" / "hostpanel_api_http").glob("*.py")  # noqa: F405
        )
        self.assertNotIn("wsgiref.simple_server", source)
        self.assertNotIn("http.server", source)
        self.assertNotIn("socket.listen", source)
        self.assertNotIn("serve_forever", source)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
