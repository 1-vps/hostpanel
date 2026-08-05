from __future__ import annotations

from tests.webhook_test_support import *  # noqa: F403


class WebhookUrlPolicyTests(unittest.TestCase):  # noqa: F405
    def test_canonical_public_https_url_is_accepted(self):
        self.assertEqual(
            normalize_webhook_url("https://hooks.example.net/hostpanel/v1"),  # noqa: F405
            ("https://hooks.example.net/hostpanel/v1", "hooks.example.net", 443, "/hostpanel/v1"),
        )

    def test_unsafe_or_noncanonical_urls_are_rejected(self):
        invalid = (
            "http://hooks.example.net/path",
            "https://user:pass@hooks.example.net/path",
            "https://hooks.example.net/path?token=secret",
            "https://hooks.example.net/path#fragment",
            "https://hooks.example.net:444/path",
            "https://127.0.0.1/path",
            "https://localhost/path",
            "https://service.internal/path",
            "https://hooks.example.net/a/../b",
            "https://HOOKS.example.net/path",
            "https://hooks.example.net/%2e%2e/private",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):  # noqa: F405
                normalize_webhook_url(value)  # noqa: F405

    def test_peer_addresses_must_be_globally_routable(self):
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "192.0.2.1"):
            with self.subTest(value=value), self.assertRaises(ValidationError):  # noqa: F405
                validate_peer_ip(value)  # noqa: F405
        self.assertEqual(validate_peer_ip("93.184.216.34"), "93.184.216.34")  # noqa: F405
        self.assertEqual(validate_peer_ip("2606:4700:4700::1111"), "2606:4700:4700::1111")  # noqa: F405

    def test_every_dns_answer_must_be_public_and_unique(self):
        self.assertEqual(validate_resolved_addresses(("93.184.216.34", "2606:4700:4700::1111")), ("93.184.216.34", "2606:4700:4700::1111"))  # noqa: F405
        for values in ((), ("93.184.216.34", "10.0.0.1"), ("93.184.216.34", "93.184.216.34")):
            with self.subTest(values=values), self.assertRaises(ValidationError):  # noqa: F405
                validate_resolved_addresses(values)  # noqa: F405


if __name__ == "__main__":
    unittest.main()  # noqa: F405
