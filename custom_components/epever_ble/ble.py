"""BLE communication with EPEVER charge controllers via raw L2CAP ATT sockets.

Uses raw L2CAP sockets (same approach as gatttool) to bypass BlueZ's GATT
service discovery, which the HN-series BLE module cannot handle.

Requires Linux with BlueZ 5.x and CAP_NET_ADMIN/CAP_NET_RAW or root.
"""

import ctypes
import ctypes.util
import logging
import os
import select
import socket
import struct
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# --- Modbus CRC16 ---


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_modbus_read(slave: int, func: int, start_reg: int, count: int) -> bytes:
    frame = struct.pack(">BBHH", slave, func, start_reg, count)
    crc = modbus_crc16(frame)
    frame += struct.pack("<H", crc)
    return frame


def verify_modbus_crc(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return modbus_crc16(data[:-2]) == struct.unpack("<H", data[-2:])[0]


def parse_read_registers_response(
    response: Optional[bytes], slave: int, func: int, start: int, count: int
) -> Optional[list[int]]:
    """Validate a Modbus register read response and return its values.

    Shared by the standalone L2CAP transport and the Home Assistant transport,
    so both accept exactly the same frames. The response must answer the
    request that was sent: matching slave ID and function code, a valid CRC and
    exactly ``count`` registers. Bytes after the first complete frame are
    ignored. Returns None for anything else, including Modbus exceptions.
    """
    if not response or len(response) < 5:
        return None

    if response[1] == func | 0x80:
        frame = response[:5]
        if verify_modbus_crc(frame) and frame[0] == slave:
            _LOGGER.warning(
                "Modbus exception %d for register 0x%04x", frame[2], start
            )
        return None

    frame = response[: response[2] + 5]
    if len(frame) < response[2] + 5 or not verify_modbus_crc(frame):
        _LOGGER.debug("Discarding corrupt response for register 0x%04x", start)
        return None
    if frame[0] != slave or frame[1] != func:
        _LOGGER.debug("Discarding foreign response for register 0x%04x", start)
        return None

    byte_count = frame[2]
    if byte_count != count * 2:
        _LOGGER.debug(
            "Discarding response with %d bytes for register 0x%04x, expected %d",
            byte_count,
            start,
            count * 2,
        )
        return None

    payload = frame[3 : 3 + byte_count]
    return [
        struct.unpack(">H", payload[offset : offset + 2])[0]
        for offset in range(0, byte_count, 2)
    ]


# --- ATT protocol opcodes ---

ATT_ERROR_RESPONSE = 0x01
ATT_EXCHANGE_MTU_REQUEST = 0x02
ATT_EXCHANGE_MTU_RESPONSE = 0x03
ATT_WRITE_REQUEST = 0x12
ATT_WRITE_RESPONSE = 0x13
ATT_WRITE_COMMAND = 0x52
ATT_HANDLE_VALUE_NOTIFICATION = 0x1B

# --- BLE handles (from GATT discovery on CPN 7810) ---

WRITE_HANDLE = 0x001E
NOTIFY_HANDLE = 0x0010
NOTIFY_CCCD_1 = 0x0011
NOTIFY_CCCD_2 = 0x001F
NOTIFY_CCCD_3 = 0x0027

# --- L2CAP / Bluetooth constants ---

BDADDR_LE_PUBLIC = 1
BDADDR_LE_RANDOM = 2
L2CAP_CID_ATT = 4
SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1

ATT_DEFAULT_MTU = 23
ATT_CLIENT_MTU = 247


def _build_sockaddr_l2(addr_bytes: bytes, cid: int, bdaddr_type: int) -> bytes:
    """Build a sockaddr_l2 structure for L2CAP BLE connections."""
    return struct.pack(
        "<HH6sHBx",
        socket.AF_BLUETOOTH,
        0,
        addr_bytes,
        cid,
        bdaddr_type,
    )


class L2capBLE:
    """BLE communication via raw L2CAP ATT socket.

    Replicates the same syscall sequence as gatttool: creates an L2CAP
    SEQPACKET socket, binds with CID=4 (ATT) and LE address type, then
    connects directly to the device. This bypasses BlueZ's automatic
    GATT service discovery, which the HN-series BLE module can't handle.
    """

    def __init__(self, address: str, addr_type: str = "public"):
        self.address = address
        self.addr_type = addr_type
        self.connected = False
        self.mtu = ATT_DEFAULT_MTU
        self._sock: Optional[socket.socket] = None
        self._libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    def connect(self) -> bool:
        _LOGGER.debug("Connecting to %s", self.address)

        bdaddr_type = (
            BDADDR_LE_RANDOM if self.addr_type == "random" else BDADDR_LE_PUBLIC
        )
        addr_bytes = bytes(reversed([int(x, 16) for x in self.address.split(":")]))

        self._sock = socket.socket(
            socket.AF_BLUETOOTH,
            socket.SOCK_SEQPACKET,
            socket.BTPROTO_L2CAP,
        )

        bind_sa = _build_sockaddr_l2(b"\x00" * 6, L2CAP_CID_ATT, bdaddr_type)
        ret = self._libc.bind(
            self._sock.fileno(),
            ctypes.create_string_buffer(bind_sa),
            len(bind_sa),
        )
        if ret != 0:
            err = ctypes.get_errno()
            _LOGGER.error("Bind failed: %s", os.strerror(err))
            self._sock.close()
            self._sock = None
            return False

        self._sock.setsockopt(
            SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_LOW, 0)
        )

        self._sock.setblocking(False)
        conn_sa = _build_sockaddr_l2(addr_bytes, L2CAP_CID_ATT, bdaddr_type)
        self._libc.connect(
            self._sock.fileno(),
            ctypes.create_string_buffer(conn_sa),
            len(conn_sa),
        )
        err = ctypes.get_errno()

        if err not in (0, 115):  # 0=OK, 115=EINPROGRESS
            _LOGGER.error("Connect failed: %s", os.strerror(err))
            self._sock.close()
            self._sock = None
            return False

        _, wready, _ = select.select([], [self._sock], [], 10.0)
        if not wready:
            _LOGGER.error("Connection timed out")
            self._sock.close()
            self._sock = None
            return False

        so_err = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if so_err != 0:
            _LOGGER.error("Connection failed: %s", os.strerror(so_err))
            self._sock.close()
            self._sock = None
            return False

        self._sock.setblocking(True)
        self.connected = True
        _LOGGER.info("Connected to %s", self.address)
        self.mtu = self.exchange_mtu()
        return True

    def exchange_mtu(self, mtu: int = ATT_CLIENT_MTU) -> int:
        """Negotiate a larger ATT MTU and return the agreed value.

        At the default MTU of 23 a notification carries at most 20 bytes, one
        byte short of an 8-register Modbus reply (21 bytes). Home Assistant's
        Bluetooth stack negotiates the MTU by itself; the raw ATT socket has
        to ask. Falls back to the default if the device refuses or is silent.
        """
        try:
            self._sock.send(struct.pack("<BH", ATT_EXCHANGE_MTU_REQUEST, mtu))
            self._sock.settimeout(3.0)
            while True:
                resp = self._sock.recv(512)
                if resp and resp[0] == ATT_EXCHANGE_MTU_RESPONSE and len(resp) >= 3:
                    agreed = min(mtu, struct.unpack("<H", resp[1:3])[0])
                    _LOGGER.debug("ATT MTU negotiated: %d", agreed)
                    return agreed
                if resp and resp[0] == ATT_ERROR_RESPONSE:
                    break
        except (socket.timeout, OSError) as err:
            _LOGGER.debug("ATT MTU exchange failed: %s", err)
        finally:
            self._sock.settimeout(None)
        _LOGGER.debug("ATT MTU stays at %d", ATT_DEFAULT_MTU)
        return ATT_DEFAULT_MTU

    def enable_notifications(self) -> bool:
        """Enable notifications by writing 0x0100 to the CCCD handles."""
        enable_value = b"\x01\x00"
        for cccd in [NOTIFY_CCCD_1, NOTIFY_CCCD_2, NOTIFY_CCCD_3]:
            pdu = struct.pack("<BH", ATT_WRITE_REQUEST, cccd) + enable_value
            self._sock.send(pdu)
            self._sock.settimeout(3.0)
            try:
                resp = self._sock.recv(512)
                if resp[0] != ATT_WRITE_RESPONSE:
                    _LOGGER.warning(
                        "Unexpected response for CCCD 0x%04x: %s", cccd, resp.hex()
                    )
            except socket.timeout:
                _LOGGER.warning("No response for CCCD 0x%04x", cccd)
        self._sock.settimeout(None)
        return True

    def send_modbus(self, frame: bytes, timeout: float = 3.0) -> Optional[bytes]:
        if not self._sock:
            return None

        # Drain stale notifications
        self._sock.setblocking(False)
        while True:
            try:
                self._sock.recv(512)
            except (BlockingIOError, OSError):
                break
        self._sock.setblocking(True)

        pdu = struct.pack("<BH", ATT_WRITE_COMMAND, WRITE_HANDLE) + frame
        self._sock.send(pdu)

        response = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self._sock], [], [], min(remaining, 0.3))
            if ready:
                data = self._sock.recv(512)
                if data and data[0] == ATT_HANDLE_VALUE_NOTIFICATION:
                    handle = struct.unpack("<H", data[1:3])[0]
                    if handle == NOTIFY_HANDLE:
                        response.extend(data[3:])
                        deadline = time.monotonic() + 0.8

        return bytes(response) if response else None

    def read_input_registers(
        self, start: int, count: int, slave: int = 1
    ) -> Optional[list[int]]:
        frame = build_modbus_read(slave, 0x04, start, count)
        response = self.send_modbus(frame)
        return parse_read_registers_response(response, slave, 0x04, start, count)

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connected = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.disconnect()
