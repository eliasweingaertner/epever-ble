"""DataUpdateCoordinator for EPEVER BLE."""

import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ha_ble import HomeAssistantBLE
from .reader import (
    LOAD_CONTROL_COIL,
    LOAD_MODE_MANUAL,
    LOAD_MODES,
    async_read_all_data,
    async_read_settings,
)

_LOGGER = logging.getLogger(__name__)

# Settings change rarely; a failed read is retried sooner than a good one.
SETTINGS_INTERVAL = 3600.0
SETTINGS_RETRY = 300.0


class EPEVERBLECoordinator(DataUpdateCoordinator):
    """Coordinator that polls an EPEVER charge controller over BLE."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        scan_interval: int,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=f"EPEVER BLE {address}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._address = address
        self._ble = HomeAssistantBLE(hass, address)
        self._settings: dict = {}
        self._settings_next = 0.0

    async def _async_update_data(self) -> dict:
        """Read controller data through Home Assistant's Bluetooth manager."""
        try:
            await self._ble.connect()
            data = await async_read_all_data(self._ble)
        except Exception as err:
            _LOGGER.debug("Read failed, will reconnect: %s", err)
            await self._ble.disconnect()
            raise UpdateFailed(f"Read failed: {err}") from err

        if not data:
            await self._ble.disconnect()
            raise UpdateFailed("No data received from controller")

        await self._async_refresh_settings()
        return {**data, **self._settings}

    async def _async_refresh_settings(self) -> None:
        """Re-read the controller settings when they are due."""
        now = time.monotonic()
        if now < self._settings_next:
            return
        try:
            settings = await async_read_settings(self._ble)
        except Exception as err:  # a settings failure must not fail the poll
            _LOGGER.debug("Reading controller settings failed: %s", err)
            settings = {}
        if settings:
            self._settings = settings
            self._settings_next = now + SETTINGS_INTERVAL
        else:
            self._settings_next = now + SETTINGS_RETRY

    async def async_set_load(self, on: bool) -> None:
        """Switch the load output through the manual load control coil."""
        mode = (self.data or {}).get("load_mode")
        if mode is not None and mode != LOAD_MODES[LOAD_MODE_MANUAL]:
            raise HomeAssistantError(
                f"The load output is in '{mode}' mode; "
                f"switching it requires '{LOAD_MODES[LOAD_MODE_MANUAL]}' mode"
            )

        try:
            await self._ble.connect()
            confirmed = await self._ble.write_coil(LOAD_CONTROL_COIL, on)
        except Exception as err:
            await self._ble.disconnect()
            raise HomeAssistantError(f"Could not switch the load output: {err}") from err

        if not confirmed:
            raise HomeAssistantError(
                "The controller did not confirm the load switch command"
            )

        if self.data is not None:
            self.async_set_updated_data({**self.data, "load_on": on})
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Disconnect BLE on shutdown."""
        await self._ble.disconnect()
        await super().async_shutdown()
