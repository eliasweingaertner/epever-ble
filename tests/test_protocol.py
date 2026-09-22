import struct

import epever_ble
from epever_ble import (
    build_modbus_read,
    modbus_crc16,
    parse_read_registers_response,
    verify_modbus_crc,
)


def _response(slave: int, func: int, registers: list[int]) -> bytes:
    body = bytes([slave, func, len(registers) * 2]) + b"".join(
        struct.pack(">H", value) for value in registers
    )
    return body + struct.pack("<H", modbus_crc16(body))


def _exception(slave: int, func: int, code: int) -> bytes:
    body = bytes([slave, func | 0x80, code])
    return body + struct.pack("<H", modbus_crc16(body))


def test_build_modbus_read_matches_known_frame() -> None:
    frame = build_modbus_read(slave=1, func=4, start_reg=0x3100, count=1)

    assert frame.hex() == "0104310000013f36"
    assert verify_modbus_crc(frame)


def test_verify_modbus_crc_rejects_short_or_modified_frame() -> None:
    assert not verify_modbus_crc(b"\x01\x04\x00")

    frame = bytearray(build_modbus_read(1, 4, 0x3100, 1))
    frame[3] ^= 0x01
    assert not verify_modbus_crc(bytes(frame))


def test_modbus_crc16_known_vector() -> None:
    assert modbus_crc16(bytes.fromhex("010431000001")) == 0x363F


def test_parse_response_returns_requested_registers() -> None:
    frame = _response(1, 0x04, [2611, 0, 75])

    assert parse_read_registers_response(frame, 1, 0x04, 0x3100, 3) == [2611, 0, 75]


def test_parse_response_ignores_bytes_after_first_frame() -> None:
    frame = _response(1, 0x04, [2611, 42]) + b""

    assert parse_read_registers_response(frame, 1, 0x04, 0x3100, 2) == [2611, 42]


def test_parse_response_rejects_corrupt_crc() -> None:
    frame = bytearray(_response(1, 0x04, [2611, 42]))
    frame[4] ^= 0x01

    assert parse_read_registers_response(bytes(frame), 1, 0x04, 0x3100, 2) is None


def test_parse_response_rejects_truncated_frame() -> None:
    frame = _response(1, 0x04, [2611, 42, 7, 8])[:-3]

    assert parse_read_registers_response(frame, 1, 0x04, 0x3100, 4) is None


def test_parse_response_rejects_register_count_mismatch() -> None:
    # A stale reply to an earlier request with a different length.
    frame = _response(1, 0x04, [2611, 42, 7, 8])

    assert parse_read_registers_response(frame, 1, 0x04, 0x3108, 2) is None


def test_parse_response_rejects_foreign_slave_and_function() -> None:
    assert parse_read_registers_response(_response(2, 0x04, [1]), 1, 0x04, 0x3100, 1) is None
    assert parse_read_registers_response(_response(1, 0x03, [1]), 1, 0x04, 0x3100, 1) is None


def test_parse_response_rejects_modbus_exception() -> None:
    assert parse_read_registers_response(_exception(1, 0x04, 2), 1, 0x04, 0x3100, 1) is None


def test_parse_response_rejects_empty_and_short_input() -> None:
    assert parse_read_registers_response(None, 1, 0x04, 0x3100, 1) is None
    assert parse_read_registers_response(b"", 1, 0x04, 0x3100, 1) is None


def test_l2cap_transport_validates_responses() -> None:
    """The standalone CLI transport must reject what the HA transport rejects."""
    ble = object.__new__(epever_ble.L2capBLE)  # skip socket setup
    good = _response(1, 0x04, [2611, 42])

    ble.send_modbus = lambda frame, timeout=3.0: good
    assert ble.read_input_registers(0x3108, 2) == [2611, 42]

    corrupt = bytearray(good)
    corrupt[3] ^= 0xFF
    ble.send_modbus = lambda frame, timeout=3.0: bytes(corrupt)
    assert ble.read_input_registers(0x3108, 2) is None

    ble.send_modbus = lambda frame, timeout=3.0: good
    assert ble.read_input_registers(0x3108, 4) is None


class FakeSocket:
    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, _value) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        if not self.replies:
            raise epever_ble._ble_mod.socket.timeout()
        return self.replies.pop(0)


def test_exchange_mtu_uses_the_smaller_of_both_sides() -> None:
    ble = object.__new__(epever_ble.L2capBLE)
    ble._sock = FakeSocket([b"\x1b\x10\x00\x01", b"\x03\xf7\x00"])

    assert ble.exchange_mtu(512) == 247
    assert ble._sock.sent == [b"\x02\x00\x02"]


def test_exchange_mtu_falls_back_to_default() -> None:
    ble = object.__new__(epever_ble.L2capBLE)
    ble._sock = FakeSocket([b"\x01\x02\x00\x00\x06"])  # error response
    assert ble.exchange_mtu() == 23

    ble._sock = FakeSocket([])  # device stays silent
    assert ble.exchange_mtu() == 23
