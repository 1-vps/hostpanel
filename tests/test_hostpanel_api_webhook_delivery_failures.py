from __future__ import annotations

import json

from tests.webhook_test_support import *  # noqa: F403

class WebhookDeliveryFailureTests(unittest.TestCase):  # noqa: F405
    def test_destination_secret_and_subscription_failures_do_not_call_transport(self):
            connection, destinations = destination_store()  # noqa: F405
            destinations.disable_destination("destination-a", tenant_id="tenant-a", expected_version=1, now=NOW)
            transport = Transport()
            outbox = FakeOutbox([FakeEvent()])  # noqa: F405
            outcome = runner(outbox=outbox, destinations=destinations, transport=transport).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(transport.requests, [])
            fail = next(call for call in outbox.calls if call[0] == "failed")
            self.assertEqual(fail[3], "webhook destination is disabled")
            connection.close()

            connection, destinations = destination_store()  # noqa: F405
            outbox = FakeOutbox([FakeEvent(event_type="mail.created")])  # noqa: F405
            outcome = runner(outbox=outbox, destinations=destinations, transport=transport).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(next(call for call in outbox.calls if call[0] == "failed")[3], "webhook destination is not subscribed")
            connection.close()

            connection, destinations = destination_store()  # noqa: F405
            outcome = runner(destinations=destinations, secrets=Secrets(values={}), transport=transport).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "failed")
            connection.close()

    def test_tls_redirect_and_private_peer_fail_terminally(self):
            responses = (
                DeliveryResponse(200, {}, "93.184.216.34", False, ("93.184.216.34",)),
                DeliveryResponse(200, {}, "93.184.216.34", True, ("93.184.216.34",), redirected=True),
                DeliveryResponse(200, {}, "10.0.0.1", True, ("10.0.0.1",)),
            )
            for response in responses:
                with self.subTest(response=response):
                    outbox = FakeOutbox([FakeEvent()])  # noqa: F405
                    outcome = runner(outbox=outbox, transport=Transport(response)).run_once()  # noqa: F405
                    self.assertEqual(outcome.status, "failed")
                    self.assertNotIn("delivered", [call[0] for call in outbox.calls])

    def test_response_body_and_secret_are_never_persisted(self):
            secret = b"highly-sensitive-secret-material!!"
            outbox = FakeOutbox([FakeEvent()])  # noqa: F405
            outcome = runner(
                outbox=outbox,
                secrets=Secrets({"vault:webhooks/destination-a": secret}),
                transport=Transport(DeliveryResponse(500, {}, "93.184.216.34", True, ("93.184.216.34",), body_bytes_read=4096)),
            ).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "retry_wait")
            dump = repr(outbox.calls)
            self.assertNotIn(secret.decode(), dump)
            self.assertNotIn("response body", dump)

    def test_lease_loss_during_claim_mark_or_fail_performs_no_second_transition(self):
            claim = FakeOutbox([FakeEvent()]); claim.fail_operation = "claim"  # noqa: F405
            self.assertEqual(runner(outbox=claim).run_once().status, "lease_lost")  # noqa: F405
            mark = FakeOutbox([FakeEvent()]); mark.fail_operation = "delivered"  # noqa: F405
            self.assertEqual(runner(outbox=mark).run_once().status, "lease_lost")  # noqa: F405
            self.assertNotIn("failed", [call[0] for call in mark.calls])
            fail = FakeOutbox([FakeEvent()]); fail.fail_operation = "failed"  # noqa: F405
            outcome = runner(outbox=fail, transport=Transport(DeliveryResponse(400, {}, "93.184.216.34", True, ("93.184.216.34",)))).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "lease_lost")

    def test_retry_backoff_is_bounded(self):
            outbox = FakeOutbox([FakeEvent(attempts=40)])  # noqa: F405
            outcome = runner(outbox=outbox, transport=Transport(error=TransientDeliveryError("timeout")), retry_base_seconds=10, retry_max_seconds=300).run_once()  # noqa: F405
            self.assertEqual(outcome.status, "retry_wait")
            fail = next(call for call in outbox.calls if call[0] == "failed")
            self.assertEqual(fail[4] - fail[5], 300)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
