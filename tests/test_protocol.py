import struct

import epever_ble
from epever_ble import (
    build_modbus_read,
    build_modbus_write_coil,
    modbus_crc16,
    parse_read_coils_response,
    parse_read_registers_response,
    parse_write_coil_response,
    verify_modbus_crc,
)

modbus_frame_length = epever_ble._ble_mod.modbus_frame_length


def _crc(body: bytes) -> bytes:
    return body + struct.pack("<H", modbus_crc16(body))


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


def test_build_modbus_write_coil_matches_known_frames() -> None:
    assert build_modbus_write_coil(1, 0x0002, True).hex() == "01050002ff002dfa"
    assert build_modbus_write_coil(1, 0x0002, False).hex() == "0105000200006c0a"


def test_modbus_frame_length_by_function() -> None:
    assert modbus_frame_length(b"\x01") is None
    assert modbus_frame_length(b"\x01\x04") is None
    assert modbus_frame_length(b"\x01\x04\x04") == 9
    assert modbus_frame_length(b"\x01\x01\x01") == 6
    assert modbus_frame_length(b"\x01\x05") == 8
    assert modbus_frame_length(b"\x01\x84") == 5


def test_parse_read_coils_response_decodes_bits() -> None:
    off = _crc(bytes([1, 0x01, 1, 0x00]))
    on = _crc(bytes([1, 0x01, 1, 0x01]))
    mixed = _crc(bytes([1, 0x01, 2, 0b00000101, 0b00000001]))

    assert parse_read_coils_response(off, 1, 0x0002, 1) == [False]
    assert parse_read_coils_response(on, 1, 0x0002, 1) == [True]
    assert parse_read_coils_response(mixed, 1, 0x0000, 9) == [
        True, False, True, False, False, False, False, False, True
    ]


def test_parse_read_coils_response_rejects_bad_frames() -> None:
    wrong_size = _crc(bytes([1, 0x01, 2, 0x01, 0x00]))
    corrupt = bytearray(_crc(bytes([1, 0x01, 1, 0x01])))
    corrupt[3] ^= 0x01

    assert parse_read_coils_response(wrong_size, 1, 0x0002, 1) is None
    assert parse_read_coils_response(bytes(corrupt), 1, 0x0002, 1) is None
    assert parse_read_coils_response(_exception(1, 0x01, 2), 1, 0x0002, 1) is None


def test_parse_write_coil_response_requires_exact_echo() -> None:
    echo_on = build_modbus_write_coil(1, 0x0002, True)

    assert parse_write_coil_response(echo_on, 1, 0x0002, True)
    assert not parse_write_coil_response(echo_on, 1, 0x0002, False)
    assert not parse_write_coil_response(echo_on, 1, 0x0003, True)
    assert not parse_write_coil_response(_exception(1, 0x05, 4), 1, 0x0002, True)
    assert not parse_write_coil_response(None, 1, 0x0002, True)


def test_l2cap_transport_reads_and_writes_coils() -> None:
    ble = object.__new__(epever_ble.L2capBLE)
    sent: list[bytes] = []

    def reply(frame, timeout=3.0):
        sent.append(frame)
        if frame[1] == 0x05:
            return frame  # write single coil answers with an echo
        if frame[1] == 0x01:
            return _crc(bytes([1, 0x01, 1, 0x01]))
        return _response(1, 0x03, [0])

    ble.send_modbus = reply

    assert ble.read_holding_registers(0x903D, 1) == [0]
    assert ble.read_coils(0x0002) == [True]
    assert ble.write_coil(0x0002, False)
    assert [frame.hex() for frame in sent] == [
        build_modbus_read(1, 0x03, 0x903D, 1).hex(),
        build_modbus_read(1, 0x01, 0x0002, 1).hex(),
        "0105000200006c0a",
    ]


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
