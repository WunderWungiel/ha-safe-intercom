"""Constants for the SAFE Intercom integration."""

from collections.abc import Mapping
from typing import Any, Final

DOMAIN: Final = "safe_intercom"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_PORT: Final = 80
MANUFACTURER: Final = "SAFE"

CONF_ENABLED_RELAYS: Final = "enabled_relays"
"""Option: 1-based numbers of the controller relays exposed as entities."""

RELAY_KEY_PREFIX: Final = "relay_"
"""Prefix of relay keys, used in unique IDs and the relay selection form."""

SUPPORTED_RELAYS: Final = (1, 2, 3)
"""Relays of the 3n controller (the only controller type supported so far)."""


def enabled_relays(options: Mapping[str, Any]) -> list[int]:
    """Return the enabled relays; entries without the option expose all of them."""
    return list(options.get(CONF_ENABLED_RELAYS, SUPPORTED_RELAYS))


def relay_key(number: int) -> str:
    """Return the key for the 1-based relay `number`, e.g. `relay_3`."""
    return f"{RELAY_KEY_PREFIX}{number}"
