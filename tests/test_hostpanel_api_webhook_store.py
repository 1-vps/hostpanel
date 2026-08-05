"""Aggregate split webhook regression suites."""

from tests.test_hostpanel_api_webhook_store_admin import WebhookDestinationStoreAdminTests
from tests.test_hostpanel_api_webhook_store_consistency import WebhookDestinationStoreConsistencyTests

__all__ = (
    "WebhookDestinationStoreAdminTests",
    "WebhookDestinationStoreConsistencyTests",
)
