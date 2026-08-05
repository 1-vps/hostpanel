from __future__ import annotations

import json

from tests.webhook_test_support import *  # noqa: F403

class WebhookDeliveryCoreTests(unittest.TestCase):  # noqa: F405
    def test_success_builds_canonical_signed_request_and_marks_delivered(self):
            outbox = FakeOutbox([FakeEvent()])  # noqa: F405
            transport = Transport(DeliveryResponse(202, {"Content-Type": "text/plain"}, "93.184.216.34", True, ("93.184.216.34",), body_bytes_read=12))  # noqa: F405
            delivery = runner(outbox=outbox, transport=transport)  # noqa: F405
            outcome = delivery.run_once()
            self.assertEqual(outcome.status, "delivered")
            self.assertEqual(outcome.http_status, 202)
            request = transport.requests[0]
            self.assertEqual(request.url, "https://hooks.example.net/hostpanel")
            self.assertFalse(request.allow_redirects)
            self.assertEqual(request.connect_timeout_seconds, 5)
            self.assertEqual(request.read_timeout_seconds, 10)
            payload = json.loads(request.body)
            self.assertEqual(payload["id"], FakeEvent().event_id)  # noqa: F405
            self.assertEqual(payload["type"], "site.updated")
            self.assertEqual(payload["data"], {"site_id": "site-a"})
            headers = dict(request.headers)
            self.assertEqual(headers["X-HostPanel-Event-Id"], FakeEvent().event_id)  # noqa: F405
            self.assertRegex(headers["X-HostPanel-Signature"], r"t=\d+,v1=[0-9a-f]{64}\Z")
            self.assertEqual([call[0] for call in outbox.calls], ["claim", "delivered"])

    def test_idle_queue_returns_idle(self):
            self.assertEqual(runner(outbox=FakeOutbox()).run_once().status, "idle")

    def test_retryable_statuses_use_retry_after_or_bounded_backoff(self):
            cases = (
                (408, {}, 5),
                (425, {}, 5),
                (429, {"Retry-After": "30"}, 30),
                (503, {}, 5),
                (429, {"Retry-After": "99999"}, 5),
                (429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, 5),
            )
            for status, response_headers, delay in cases:
                with self.subTest(status=status, response_headers=response_headers):
                    outbox = FakeOutbox([FakeEvent()])  # noqa: F405
                    clock = Clock()  # noqa: F405
                    outcome = runner(
                        outbox=outbox,
                        transport=Transport(DeliveryResponse(status, response_headers, "93.184.216.34", True, ("93.184.216.34",))),  # noqa: F405
                        clock=clock,
                    ).run_once()
                    self.assertEqual(outcome.status, "retry_wait")
                    fail = next(call for call in outbox.calls if call[0] == "failed")
                    self.assertEqual(fail[4] - fail[5], delay)
                    self.assertEqual(fail[3], f"webhook returned HTTP {status}")

    def test_permanent_statuses_fail_without_retry(self):
            for status in (300, 301, 400, 401, 403, 404, 409, 410, 422):
                with self.subTest(status=status):
                    outbox = FakeOutbox([FakeEvent()])  # noqa: F405
                    outcome = runner(outbox=outbox, transport=Transport(DeliveryResponse(status, {}, "93.184.216.34", True, ("93.184.216.34",)))).run_once()  # noqa: F405
                    self.assertEqual(outcome.status, "failed")
                    fail = next(call for call in outbox.calls if call[0] == "failed")
                    self.assertIsNone(fail[4])

    def test_transient_transport_error_retries_but_permanent_error_does_not(self):
            transient = runner(transport=Transport(error=TransientDeliveryError("timeout"))).run_once()  # noqa: F405
            permanent = runner(transport=Transport(error=PermanentDeliveryError("certificate"))).run_once()  # noqa: F405
            self.assertEqual(transient.status, "retry_wait")
            self.assertEqual(permanent.status, "failed")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
