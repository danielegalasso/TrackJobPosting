"""Job inspectors for public Applicant Tracking System feeds.

Importing this package registers every connector. The registry is built by
the `@register` decorator on each class, so the modules must be imported
here — nothing else imports them, and an empty registry would make every
configured target fail with "unknown source type".
"""

# The connector modules are imported for their registration side effect and
# re-exported for convenience.
from .ashby import AshbyConnector
from .base import CONNECTORS, Connector, ConnectorError, get_connector
from .greenhouse import GreenhouseConnector
from .lever import LeverConnector

__all__ = [
    "AshbyConnector",
    "CONNECTORS",
    "Connector",
    "ConnectorError",
    "GreenhouseConnector",
    "LeverConnector",
    "get_connector",
]
