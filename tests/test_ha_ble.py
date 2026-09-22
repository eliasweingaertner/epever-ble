"""Framing tests for the Home Assistant transport, without Home Assistant.

ha_ble.py imports bleak and homeassistant at module level. Both are stubbed
here so the request/response framing can be tested on its own.
"""

import asyncio
import importlib
import pathlib
import struct
import sys
import types

import pytest

from epever_ble import build_modbus_write_coil, modbus_crc16

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "epever_ble"


def _stub(name: str, **attrs) -> None:
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules.setdefault(name, module)


@pytest.fixture(scope="module")
def ha_ble():
    _stub("bleak", BleakClient=object)
    _stub("bleak.backends")
    _stub("bleak.backends.characteristic", BleakGATTCharacteristic=object)
    _stub("bleak_retry_connector", establish_connection=None)
    _stub("homeassistant")
    _stub("homeassistant.components", bluetooth=None)
    _stub("homeassistant.core", HomeAssistant=object)

    # Load the package without running its __init__.py (needs a full HA).
    package = types.ModuleType("custom_components.epever_ble")
    package.__path__ = [str(COMPONENT)]
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    sys.modules["custom_components.epever_ble"] = package
    return importlib.import_module("custom_components.epever_ble.ha_ble")


def _crc(body: bytes) -> bytes:
    return body + struct.pack("<H", modbus_crc16(body))


class FakeClient:
    """Answers each write with notifications, delivered in small chunks."""

    is_connected = True

    def __init__(self, transport, replies) -> None:
        self.transport = transport
        self.replies = replies
        self.written: list[bytes] = []

    async def write_gatt_char(self, _char, frame, response=False) -> None:
        self.written.append(bytes(frame))
        for payload in self.replies.pop(0):
            for offset in range(0, len(payload), 3):
                self.transport._notification(None, bytearray(payload[offset : offset + 3]))


def _transport(ha_ble, replies):
    ble = ha_ble.HomeAssistantBLE(None, "AA:BB:CC:DD:EE:FF")
    ble._client = FakeClient(ble, replies)
    ble._write_characteristic = object()
    return ble


def test_write_coil_accepts_echo_after_stale_frame(ha_ble) -> None:
    stale = _crc(bytes([1, 0x04, 2, 0x0A, 0x33]))
    ble = _transport(ha_ble, [[stale, build_modbus_write_coil(1, 0x0002, True)]])

    assert asyncio.run(ble.write_coil(0x0002, True))
    assert ble._client.written == [build_modbus_write_coil(1, 0x0002, True)]


def test_read_coils_and_holding_registers(ha_ble) -> None:
    ble = _transport(
        ha_ble,
        [
            [_crc(bytes([1, 0x03, 2, 0x00, 0x00]))],
            [_crc(bytes([1, 0x01, 1, 0x00]))],
        ],
    )

    async def run():
        return (
            await ble.read_holding_registers(0x903D, 1),
            await ble.read_coils(0x0002),
        )

    assert asyncio.run(run()) == ([0], [False])


def test_write_coil_rejects_modbus_exception(ha_ble) -> None:
    ble = _transport(ha_ble, [[_crc(bytes([1, 0x85, 0x04]))]])

    assert not asyncio.run(ble.write_coil(0x0002, True))


def test_requests_are_serialised(ha_ble) -> None:
    """A switch command must not interleave with a running poll."""
    order: list[str] = []
    ble = _transport(ha_ble, [])

    async def slow_write(_char, frame, response=False):
        order.append(f"start {frame[1]:02x}")
        await asyncio.sleep(0.01)
        order.append(f"end {frame[1]:02x}")
        reply = frame if frame[1] == 0x05 else _crc(bytes([1, 0x04, 2, 0x0A, 0x33]))
        ble._notification(None, bytearray(reply))

    ble._client.write_gatt_char = slow_write

    async def run():
        await asyncio.gather(
            ble.read_input_registers(0x3108, 1), ble.write_coil(0x0002, True)
        )

    asyncio.run(run())
    assert order == ["start 04", "end 04", "start 05", "end 05"]
