"""Aggregate the split API HTTP security regression suites."""

from tests.test_hostpanel_api_http_security_cursor import (
    HostpanelApiHttpSecurityCursorTests,
)
from tests.test_hostpanel_api_http_security_requests import (
    HostpanelApiHttpSecurityRequestTests,
)

__all__ = (
    "HostpanelApiHttpSecurityCursorTests",
    "HostpanelApiHttpSecurityRequestTests",
)
