from __future__ import annotations

from tests.api_control_test_support import *  # noqa: F403



class HostpanelApiControlOutboxTests(ApiControlTestCase):  # noqa: F405
    def test_outbox_dedupe_delivery_retry_and_recovery(self) -> None:
        first = self.outbox(dedupe_key="site-created-01")
        duplicate = self.outbox(dedupe_key="site-created-01")
        self.assertFalse(duplicate.created)
        self.assertEqual(first.event.event_id, duplicate.event.event_id)
        with self.assertRaises(ConflictError):
            self.outbox(
                dedupe_key="site-created-01",
                payload={"site": "other.example"},
            )
        claimed = self.store.claim_outbox_event(worker_id="sender-a", now=NOW)
        delivered = self.store.mark_outbox_delivered(
            claimed.event_id,
            worker_id="sender-a",
            now=NOW + 1,
        )
        self.assertEqual(delivered.status, "delivered")

        retry_event = self.outbox(event_type="backup.failed", max_attempts=2).event
        first_attempt = self.store.claim_outbox_event(
            worker_id="sender-a", now=NOW + 2
        )
        self.assertEqual(first_attempt.event_id, retry_event.event_id)
        retry = self.store.fail_outbox_event(
            retry_event.event_id,
            worker_id="sender-a",
            error="timeout",
            retry_at=NOW + 10,
            now=NOW + 3,
        )
        self.assertEqual(retry.status, "retry_wait")
        second_attempt = self.store.claim_outbox_event(
            worker_id="sender-b", lease_seconds=5, now=NOW + 10
        )
        self.assertEqual(second_attempt.attempts, 2)
        recovered = self.store.claim_outbox_event(
            worker_id="sender-c", now=NOW + 16
        )
        self.assertIsNone(recovered)
        terminal = next(
            event
            for event in self.store.list_outbox_events(destination_id="webhook-primary")
            if event.event_id == retry_event.event_id
        )
        self.assertEqual(terminal.status, "failed")
        self.assertEqual(terminal.last_error, "delivery lease expired")

    def test_webhook_signature_and_replay_cache(self) -> None:
        secret = b"s" * 32
        event_id = "evt_abcdefghijklmnopqrstuv"
        payload = b'{"site":"example.com"}'
        header = sign_webhook(
            secret,
            event_id=event_id,
            payload=payload,
            timestamp=NOW,
        )
        self.assertEqual(
            verify_webhook_signature(
                secret,
                event_id=event_id,
                payload=payload,
                signature_header=header,
                now=NOW + 10,
            ),
            NOW,
        )
        self.assertEqual(
            self.store.verify_and_record_webhook(
                receiver_id="receiver-a",
                event_id=event_id,
                payload=payload,
                signature_header=header,
                secret=secret,
                now=NOW + 10,
            ),
            NOW,
        )
        with self.assertRaises(ReplayError):
            self.store.verify_and_record_webhook(
                receiver_id="receiver-a",
                event_id=event_id,
                payload=payload,
                signature_header=header,
                secret=secret,
                now=NOW + 11,
            )
        for candidate in (
            header[:-1] + ("0" if header[-1] != "0" else "1"),
            "invalid",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(AuthenticationError):
                verify_webhook_signature(
                    secret,
                    event_id=event_id,
                    payload=payload,
                    signature_header=candidate,
                    now=NOW,
                )
        with self.assertRaises(AuthenticationError):
            verify_webhook_signature(
                secret,
                event_id=event_id,
                payload=payload,
                signature_header=header,
                now=NOW + 301,
            )

    def test_expired_webhook_receipt_can_be_reused_with_a_fresh_signature(self) -> None:
        secret = b"r" * 32
        event_id = "evt_abcdefghijklmnopqrstuv"
        payload = b"{}"
        first = sign_webhook(
            secret, event_id=event_id, payload=payload, timestamp=NOW
        )
        self.store.verify_and_record_webhook(
            receiver_id="receiver-expiry",
            event_id=event_id,
            payload=payload,
            signature_header=first,
            secret=secret,
            now=NOW,
            receipt_ttl=5,
        )
        fresh = sign_webhook(
            secret, event_id=event_id, payload=payload, timestamp=NOW + 6
        )
        self.assertEqual(
            self.store.verify_and_record_webhook(
                receiver_id="receiver-expiry",
                event_id=event_id,
                payload=payload,
                signature_header=fresh,
                secret=secret,
                now=NOW + 6,
                receipt_ttl=5,
            ),
            NOW + 6,
        )


if __name__ == "__main__":
    unittest.main()  # noqa: F405
