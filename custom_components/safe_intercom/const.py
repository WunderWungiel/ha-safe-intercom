"""Constants for the SAFE Intercom integration."""

from collections.abc import Mapping
from typing import Any, Final

DOMAIN: Final = "safe_intercom"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_PORT: Final = 80
MANUFACTURER: Final = "SAFE"

CONF_ENABLED_RELAYS: Final = "enabled_relays"
"""Option: 1-based numbers of the controller relays exposed as entities."""

SUPPORTED_RELAYS: Final = (1, 2, 3)
"""Relays of the 3n controller (the only controller type supported so far)."""


def enabled_relays(options: Mapping[str, Any]) -> list[int]:
    """Return the enabled relays; entries without the option expose all of them."""
    return list(options.get(CONF_ENABLED_RELAYS, SUPPORTED_RELAYS))
