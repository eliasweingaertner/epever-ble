"""EPEVER BLE Solar Charge Controller integration for Home Assistant.

Connects to EPEVER Tracer charge controllers via their built-in BLE interface
exposes solar, battery, load, and energy data as sensor entities, and lets
the load output be switched when the controller is in manual load mode.
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import EPEVERBLECoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EPEVER BLE from a config entry."""
    coordinator = EPEVERBLECoordinator(
        hass,
        address=entry.data[CONF_MAC],
        scan_interval=entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: EPEVERBLECoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
