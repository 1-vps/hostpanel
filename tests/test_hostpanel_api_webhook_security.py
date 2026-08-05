from __future__ import annotations

from tests.webhook_test_support import *  # noqa: F403


class WebhookSecurityTests(unittest.TestCase):  # noqa: F405
    def test_destination_store_never_persists_secret_bytes(self):
        connection, store = destination_store()  # noqa: F405
        dump = " ".join(
            str(value)
            for row in connection.execute("SELECT * FROM hp_api_webhook_destinations")
            for value in row
        )
        self.assertIn("vault:webhooks/destination-a", dump)
        self.assertNotIn("ssssssss", dump)
        connection.close()

    def test_invalid_signature_callback_fails_closed(self):
        connection, destinations = destination_store()  # noqa: F405
        outbox = FakeOutbox([FakeEvent()])  # noqa: F405
        delivery = WebhookDeliveryRunner(  # noqa: F405
            outbox, destinations, Secrets(), Transport(),
            lambda *args, **kwargs: "line\nbreak",
            worker_id="delivery-a", clock=Clock(),
        )
        self.assertEqual(delivery.run_once().status, "failed")
        self.assertEqual(next(call for call in outbox.calls if call[0] == "failed")[3], "webhook delivery failed")
        connection.close()

    def test_package_has_no_network_client_dynamic_loading_or_process_surface(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "tools" / "hostpanel_api_webhooks").glob("*.py"))  # noqa: F405
        for forbidden in ("http.client", "urllib.request", "requests", "socket.create_connection", "importlib", "subprocess", "os.system", "eval(", "exec(", "serve_forever", "systemctl"):
            self.assertNotIn(forbidden, source)

    def test_delivery_request_forbids_redirects_and_has_bounded_timeouts(self):
        request = DeliveryRequest(  # noqa: F405
            "https://hooks.example.net/path", "hooks.example.net", 443, "/path", (), b"{}", 5, 10
        )
        self.assertFalse(request.allow_redirects)
        with self.assertRaises(ValidationError):  # noqa: F405
            runner(connect_timeout_seconds=0)  # noqa: F405
        with self.assertRaises(ValidationError):  # noqa: F405
            runner(read_timeout_seconds=301)  # noqa: F405

    def test_schema_tampering_is_detected(self):
        connection, store = destination_store()  # noqa: F405
        connection.execute("DROP INDEX hp_api_webhook_destinations_tenant_idx")
        connection.execute("CREATE INDEX hp_api_webhook_destinations_tenant_idx ON hp_api_webhook_destinations(destination_id)")
        with self.assertRaises(StateError):  # noqa: F405
            store.verify_schema()
        connection.close()

    def test_mixed_dns_result_and_peer_outside_result_fail_closed(self):
        response = DeliveryResponse(200, {}, "93.184.216.34", True, ("93.184.216.34", "10.0.0.1"))
        outbox = FakeOutbox([FakeEvent()])
        outcome = runner(outbox=outbox, transport=Transport(response)).run_once()
        self.assertEqual(outcome.status, "failed")
        self.assertNotIn("delivered", [call[0] for call in outbox.calls])
        with self.assertRaises(ValidationError):
            DeliveryResponse(200, {}, "93.184.216.34", True, ("1.1.1.1",))

    def test_persisted_destination_tampering_is_detected_on_read(self):
        connection, store = destination_store()
        connection.execute("UPDATE hp_api_webhook_destinations SET host='127.0.0.1' WHERE destination_id='destination-a'")
        with self.assertRaises(StateError):
            store.get_destination("destination-a")
        connection.close()


if __name__ == "__main__":
    unittest.main()  # noqa: F405
