"""DataUpdateCoordinator for EPEVER BLE."""

import logging
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
)

_LOGGER = logging.getLogger(__name__)


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

        return data

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
