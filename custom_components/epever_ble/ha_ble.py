"""Home Assistant Bluetooth transport for EPEVER controllers."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .ble import (
    FC_READ_COILS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    build_modbus_read,
    build_modbus_write_coil,
    modbus_frame_length,
    parse_read_coils_response,
    parse_read_registers_response,
    parse_write_coil_response,
)

_LOGGER = logging.getLogger(__name__)

NOTIFY_UUID = "00002b10-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "00002b14-0000-1000-8000-00805f9b34fb"


class HomeAssistantBLE:
    """Communicate through Home Assistant's local or remote BLE adapters."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._hass = hass
        self.address = address
        self._client: BleakClient | None = None
        self._notify_characteristic: BleakGATTCharacteristic | None = None
        self._write_characteristic: BleakGATTCharacteristic | None = None
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue()
        # One request/response at a time: polling and switching share the link.
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Return whether the selected HA Bluetooth path is connected."""
        return self._client is not None and self._client.is_connected

    def _disconnected(self, _client: BleakClient) -> None:
        self._client = None
        self._notify_characteristic = None
        self._write_characteristic = None

    def _notification(
        self, _sender: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        self._notifications.put_nowait(bytes(payload))

    async def connect(self) -> None:
        """Connect through the best connectable adapter known to Home Assistant."""
        if self.connected:
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self.address, connectable=True
        )
        if ble_device is None:
            raise ConnectionError(
                f"No connectable Bluetooth path is available for {self.address}"
            )

        client = await establish_connection(
            BleakClient,
            ble_device,
            ble_device.name or self.address,
            disconnected_callback=self._disconnected,
        )

        notify_characteristic = client.services.get_characteristic(NOTIFY_UUID)
        write_characteristic = client.services.get_characteristic(WRITE_UUID)
        if notify_characteristic is None or write_characteristic is None:
            await client.disconnect()
            raise ConnectionError(
                "Required EPEVER GATT characteristics are unavailable"
            )

        self._client = client
        self._notify_characteristic = notify_characteristic
        self._write_characteristic = write_characteristic
        await client.start_notify(notify_characteristic, self._notification)

    async def _send_modbus(
        self,
        frame: bytes,
        expected_length: int,
        timeout: float = 3.0,
    ) -> bytes | None:
        """Send one request and return its reply of the expected length.

        Stale frames of a different length are skipped; Modbus exceptions are
        returned so the caller can report them.
        """
        async with self._lock:
            if not self.connected:
                return None

            while not self._notifications.empty():
                self._notifications.get_nowait()

            assert self._client is not None
            assert self._write_characteristic is not None
            await self._client.write_gatt_char(
                self._write_characteristic, frame, response=False
            )

            response = bytearray()
            try:
                async with asyncio.timeout(timeout):
                    while True:
                        while (length := modbus_frame_length(response)) is not None:
                            if len(response) < length:
                                break
                            candidate = bytes(response[:length])
                            del response[:length]
                            if candidate[1] & 0x80 or length == expected_length:
                                return candidate
                        response.extend(await self._notifications.get())
            except TimeoutError:
                return None

    async def read_input_registers(
        self, start: int, count: int, slave: int = 1
    ) -> list[int] | None:
        """Read Modbus input registers (function 0x04)."""
        return await self._read_registers(FC_READ_INPUT_REGISTERS, start, count, slave)

    async def read_holding_registers(
        self, start: int, count: int, slave: int = 1
    ) -> list[int] | None:
        """Read Modbus holding registers (function 0x03)."""
        return await self._read_registers(
            FC_READ_HOLDING_REGISTERS, start, count, slave
        )

    async def _read_registers(
        self, func: int, start: int, count: int, slave: int
    ) -> list[int] | None:
        response = await self._send_modbus(
            build_modbus_read(slave, func, start, count),
            expected_length=count * 2 + 5,
        )
        return parse_read_registers_response(response, slave, func, start, count)

    async def read_coils(
        self, start: int, count: int = 1, slave: int = 1
    ) -> list[bool] | None:
        """Read Modbus coils (function 0x01)."""
        response = await self._send_modbus(
            build_modbus_read(slave, FC_READ_COILS, start, count),
            expected_length=(count + 7) // 8 + 5,
        )
        return parse_read_coils_response(response, slave, start, count)

    async def write_coil(self, address: int, on: bool, slave: int = 1) -> bool:
        """Write one coil (function 0x05); True once the controller echoes it."""
        response = await self._send_modbus(
            build_modbus_write_coil(slave, address, on), expected_length=8
        )
        return parse_write_coil_response(response, slave, address, on)

    async def disconnect(self) -> None:
        """Disconnect the current HA Bluetooth path."""
        client = self._client
        self._client = None
        self._notify_characteristic = None
        self._write_characteristic = None
        if client is not None and client.is_connected:
            await client.disconnect()
