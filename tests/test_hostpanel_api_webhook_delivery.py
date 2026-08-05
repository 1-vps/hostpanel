"""Aggregate split webhook regression suites."""

from tests.test_hostpanel_api_webhook_delivery_core import WebhookDeliveryCoreTests
from tests.test_hostpanel_api_webhook_delivery_failures import WebhookDeliveryFailureTests

__all__ = (
    "WebhookDeliveryCoreTests",
    "WebhookDeliveryFailureTests",
)
