"""DataUpdateCoordinator for EPEver BLE."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble import L2capBLE
from .reader import read_all_data

_LOGGER = logging.getLogger(__name__)


class EPEverBLECoordinator(DataUpdateCoordinator):
    """Coordinator that polls an EPEver charge controller over BLE."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        scan_interval: int,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=f"EPEver BLE {address}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._address = address
        self._ble: L2capBLE | None = None

    def _sync_update(self) -> dict:
        """Blocking BLE read — runs in executor thread."""
        if self._ble is None or not self._ble.connected:
            if self._ble is not None:
                self._ble.disconnect()
            self._ble = L2capBLE(self._address)
            if not self._ble.connect():
                self._ble = None
                raise UpdateFailed(f"Cannot connect to {self._address}")
            self._ble.enable_notifications()

        try:
            data = read_all_data(self._ble)
        except Exception as err:
            _LOGGER.debug("Read failed, will reconnect: %s", err)
            self._ble.disconnect()
            self._ble = None
            raise UpdateFailed(f"Read failed: {err}") from err

        if not data:
            self._ble.disconnect()
            self._ble = None
            raise UpdateFailed("No data received from controller")

        return data

    async def _async_update_data(self) -> dict:
        return await self.hass.async_add_executor_job(self._sync_update)

    async def async_shutdown(self) -> None:
        """Disconnect BLE on shutdown."""
        if self._ble is not None:
            await self.hass.async_add_executor_job(self._ble.disconnect)
            self._ble = None
        await super().async_shutdown()
