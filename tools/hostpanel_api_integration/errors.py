from __future__ import annotations


class IntegrationError(RuntimeError):
    """Base failure for the HostPanel API integration layer."""


class ConfigurationError(IntegrationError):
    """The injected foundation stores or runtime callbacks are incompatible."""


class ResourceNotFound(IntegrationError):
    """A trusted lookup did not find a tenant-bound object."""
