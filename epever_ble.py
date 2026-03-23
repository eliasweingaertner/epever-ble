#!/usr/bin/env python3
"""
EPEver Tracer CPN 7810 BLE Client

Connects to an EPEver charge controller via its built-in BLE interface,
reads Modbus registers over BLE, and displays real-time solar data.

Uses a raw L2CAP ATT socket for BLE communication — the same approach
as gatttool, but without the subprocess. This bypasses BlueZ's automatic
GATT service discovery, which causes the HN-series BLE module to
disconnect. Scanning uses the BlueZ D-Bus API.

Requires:
  - Linux with BlueZ 5.x
  - python3-dbus and python3-gi (for --scan only)

GATT layout (discovered from this device):
  Service 00002b00 (handles 0x000e-0x0028):
    Write char:  00002b14 val_handle=0x001e (WRITE_NO_RSP|NOTIFY)
    Notify char: 00002b10 val_handle=0x0010 (NOTIFY), CCCD=0x0011
    Notify char: 00002b16 val_handle=0x0026 (NOTIFY), CCCD=0x0027

  Modbus RTU frames are written to handle 0x001e via ATT Write Command.
  Responses arrive as ATT Handle Value Notifications on handle 0x0010.
  Responses may be fragmented across multiple notifications (20-byte MTU).

Usage:
    python epever_ble.py --scan                    # Find nearby BLE devices
    python epever_ble.py --addr XX:XX:XX:XX:XX:XX  # Read all data once
    python epever_ble.py --addr XX:XX:XX --loop    # Poll continuously
    python epever_ble.py --addr XX:XX:XX --raw HEX # Send raw Modbus hex
"""

import argparse
import ctypes
import ctypes.util
import os
import re
import select
import socket
import struct
import sys
import time
from typing import Optional


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
    frame = struct.pack('>BBHH', slave, func, start_reg, count)
    crc = modbus_crc16(frame)
    frame += struct.pack('<H', crc)
    return frame


def verify_modbus_crc(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return modbus_crc16(data[:-2]) == struct.unpack('<H', data[-2:])[0]


# --- ATT protocol opcodes ---

ATT_WRITE_REQUEST = 0x12
ATT_WRITE_RESPONSE = 0x13
ATT_WRITE_COMMAND = 0x52
ATT_HANDLE_VALUE_NOTIFICATION = 0x1B

# --- BLE handles (from GATT discovery) ---

WRITE_HANDLE = 0x001E       # Char 00002b14 value handle
NOTIFY_HANDLE = 0x0010      # Where responses arrive
NOTIFY_CCCD_1 = 0x0011      # CCCD for char 00002b10 (notifications)
NOTIFY_CCCD_2 = 0x001F      # CCCD for char 00002b14
NOTIFY_CCCD_3 = 0x0027      # CCCD for char 00002b16

# --- L2CAP / Bluetooth constants ---

BDADDR_LE_PUBLIC = 1
BDADDR_LE_RANDOM = 2
L2CAP_CID_ATT = 4
SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1

# --- EPEver Register Map ---

CHARGING_MODES = {0: "Not Charging", 1: "Float", 2: "Boost", 3: "Equalization"}
BATTERY_TYPES = {0: "User", 1: "Sealed", 2: "GEL", 3: "Flooded"}


def _build_sockaddr_l2(addr_bytes: bytes, cid: int, bdaddr_type: int) -> bytes:
    """Build a sockaddr_l2 structure for L2CAP BLE connections."""
    return struct.pack(
        '<HH6sHBx',
        socket.AF_BLUETOOTH,  # l2_family
        0,                    # l2_psm (0 for ATT fixed channel)
        addr_bytes,           # l2_bdaddr (reversed MAC)
        cid,                  # l2_cid
        bdaddr_type,          # l2_bdaddr_type
    )


class L2capBLE:
    """BLE communication via raw L2CAP ATT socket.

    Replicates the same syscall sequence as gatttool: creates an L2CAP
    SEQPACKET socket, binds with CID=4 (ATT) and LE address type, then
    connects directly to the device. This bypasses BlueZ's GATT service
    discovery layer, which the HN-series BLE module can't handle.
    """

    def __init__(self, address: str, addr_type: str = "public"):
        self.address = address
        self.addr_type = addr_type
        self.connected = False
        self._sock = None
        self._libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)

    def start(self):
        pass  # No setup needed — socket is created on connect

    def connect(self) -> bool:
        print(f"Connecting to {self.address}...")

        bdaddr_type = BDADDR_LE_RANDOM if self.addr_type == "random" else BDADDR_LE_PUBLIC
        addr_bytes = bytes(reversed([int(x, 16) for x in self.address.split(':')]))

        self._sock = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP,
        )

        # Bind with LE address type and ATT CID — sets the source address
        # type so the kernel allows LE connections
        bind_sa = _build_sockaddr_l2(b'\x00' * 6, L2CAP_CID_ATT, bdaddr_type)
        ret = self._libc.bind(
            self._sock.fileno(),
            ctypes.create_string_buffer(bind_sa),
            len(bind_sa),
        )
        if ret != 0:
            err = ctypes.get_errno()
            print(f"Bind failed: {os.strerror(err)}")
            return False

        # Set security level to low (no encryption required)
        self._sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack('BB', BT_SECURITY_LOW, 0))

        # Non-blocking connect — returns EINPROGRESS, then we select()
        self._sock.setblocking(False)
        conn_sa = _build_sockaddr_l2(addr_bytes, L2CAP_CID_ATT, bdaddr_type)
        self._libc.connect(
            self._sock.fileno(),
            ctypes.create_string_buffer(conn_sa),
            len(conn_sa),
        )
        err = ctypes.get_errno()

        if err not in (0, 115):  # 0=OK, 115=EINPROGRESS
            print(f"Connect failed: {os.strerror(err)}")
            self._sock.close()
            self._sock = None
            return False

        # Wait for connection to complete
        _, wready, _ = select.select([], [self._sock], [], 10.0)
        if not wready:
            print("Connection timed out.")
            self._sock.close()
            self._sock = None
            return False

        so_err = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if so_err != 0:
            print(f"Connection failed: {os.strerror(so_err)}")
            self._sock.close()
            self._sock = None
            return False

        self._sock.setblocking(True)
        self.connected = True
        print("Connected.")
        return True

    def enable_notifications(self):
        """Enable notifications by writing 0x0100 to the CCCD handles."""
        enable_value = b'\x01\x00'
        for cccd in [NOTIFY_CCCD_1, NOTIFY_CCCD_2, NOTIFY_CCCD_3]:
            # ATT Write Request to CCCD handle
            pdu = struct.pack('<BH', ATT_WRITE_REQUEST, cccd) + enable_value
            self._sock.send(pdu)
            self._sock.settimeout(3.0)
            try:
                resp = self._sock.recv(512)
                if resp[0] != ATT_WRITE_RESPONSE:
                    print(f"  Warning: Unexpected response for CCCD 0x{cccd:04x}: {resp.hex()}")
            except socket.timeout:
                print(f"  Warning: No response for CCCD 0x{cccd:04x}")
        self._sock.settimeout(None)
        return True

    def send_modbus(self, frame: bytes, timeout: float = 3.0) -> Optional[bytes]:
        if not self._sock:
            return None

        # Drain any stale notifications
        self._sock.setblocking(False)
        while True:
            try:
                self._sock.recv(512)
            except (BlockingIOError, OSError):
                break
        self._sock.setblocking(True)

        # ATT Write Command (no response) to write handle
        pdu = struct.pack('<BH', ATT_WRITE_COMMAND, WRITE_HANDLE) + frame
        self._sock.send(pdu)

        # Collect notification fragments
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
                    handle = struct.unpack('<H', data[1:3])[0]
                    if handle == NOTIFY_HANDLE:
                        response.extend(data[3:])  # Strip ATT header
                        # Extend deadline to catch more fragments
                        deadline = time.monotonic() + 0.8

        return bytes(response) if response else None

    def read_input_registers(self, start: int, count: int, slave: int = 1) -> Optional[list[int]]:
        frame = build_modbus_read(slave, 0x04, start, count)
        response = self.send_modbus(frame)

        if not response:
            return None

        if len(response) < 5:
            return None

        if response[1] & 0x80:
            error_code = response[2]
            errors = {1: "Illegal Function", 2: "Illegal Data Address",
                      3: "Illegal Data Value", 4: "Slave Device Failure"}
            print(f"  Modbus error: {errors.get(error_code, f'Code {error_code}')}")
            return None

        byte_count = response[2]
        data = response[3:3 + byte_count]

        if verify_modbus_crc(response[:3 + byte_count + 2]):
            pass  # CRC OK
        elif len(response) < 3 + byte_count + 2:
            pass  # Incomplete response (missing CRC bytes), but data may be valid

        registers = []
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                registers.append(struct.unpack('>H', data[i:i + 2])[0])
        return registers

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connected = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.disconnect()


# --- Data Reading & Display ---

def combine_32bit(low: int, high: int) -> float:
    return (high * 65536 + low) / 100.0


def signed_temp(val: int) -> float:
    if val > 32767:
        val -= 65536
    return val / 100.0


def read_all_data(ble: L2capBLE) -> tuple[dict, dict]:
    rt = {}
    energy = {}
    delay = 0.3

    # --- PV + Battery (0x3100-0x3107, 8 registers) ---
    regs = ble.read_input_registers(0x3100, 8)
    if regs:
        n = len(regs)
        if n > 0: rt["pv_voltage"] = regs[0] / 100.0
        if n > 1: rt["pv_current"] = regs[1] / 100.0
        if n > 3: rt["pv_power"] = combine_32bit(regs[2], regs[3])
        if n > 4: rt["batt_voltage"] = regs[4] / 100.0
        if n > 5: rt["batt_charge_current"] = regs[5] / 100.0
        if n > 7: rt["batt_charge_power"] = combine_32bit(regs[6], regs[7])

    # --- Load + Temp (0x310C-0x3113, 8 registers) ---
    time.sleep(delay)
    regs2 = ble.read_input_registers(0x310C, 8)
    if regs2:
        n = len(regs2)
        if n > 0: rt["load_voltage"] = regs2[0] / 100.0
        if n > 1: rt["load_current"] = regs2[1] / 100.0
        if n > 3: rt["load_power"] = combine_32bit(regs2[2], regs2[3])
        if n > 4: rt["batt_temp"] = signed_temp(regs2[4])
        if n > 5: rt["device_temp"] = signed_temp(regs2[5])

    # --- Battery SOC ---
    time.sleep(delay)
    soc_regs = ble.read_input_registers(0x311A, 2)
    if soc_regs:
        rt["batt_soc"] = soc_regs[0]

    # --- Status registers ---
    time.sleep(delay)
    status_regs = ble.read_input_registers(0x3200, 3)
    if status_regs and len(status_regs) >= 2:
        charge_mode = (status_regs[1] >> 2) & 0x03
        rt["charge_mode"] = CHARGING_MODES.get(charge_mode, f"Unknown({charge_mode})")

    # --- Generated energy: 0x330C-0x3313 (8 registers) ---
    time.sleep(delay)
    gen_regs = ble.read_input_registers(0x330C, 8)
    if gen_regs and len(gen_regs) >= 8:
        energy["gen_today"] = combine_32bit(gen_regs[0], gen_regs[1])
        energy["gen_month"] = combine_32bit(gen_regs[2], gen_regs[3])
        energy["gen_year"] = combine_32bit(gen_regs[4], gen_regs[5])
        energy["gen_total"] = combine_32bit(gen_regs[6], gen_regs[7])

    # --- Consumed energy: 0x3304-0x330B (8 registers) ---
    time.sleep(delay)
    use_regs = ble.read_input_registers(0x3304, 8)
    if use_regs and len(use_regs) >= 8:
        energy["use_today"] = combine_32bit(use_regs[0], use_regs[1])
        energy["use_month"] = combine_32bit(use_regs[2], use_regs[3])
        energy["use_year"] = combine_32bit(use_regs[4], use_regs[5])
        energy["use_total"] = combine_32bit(use_regs[6], use_regs[7])

    return rt, energy


def display_data(rt: dict, energy: dict):
    print("\n" + "=" * 55)
    print("  EPEver Tracer CPN 7810 - Live Data")
    print("=" * 55)

    if rt:
        print(f"\n  --- Solar Panel (PV) ---")
        if "pv_voltage" in rt:
            print(f"  Voltage:  {rt['pv_voltage']:>8.2f} V")
        if "pv_current" in rt:
            print(f"  Current:  {rt['pv_current']:>8.2f} A")
        if "pv_power" in rt:
            print(f"  Power:    {rt['pv_power']:>8.2f} W")

        print(f"\n  --- Battery ---")
        if "batt_voltage" in rt:
            print(f"  Voltage:  {rt['batt_voltage']:>8.2f} V")
        if "batt_charge_current" in rt:
            print(f"  Current:  {rt['batt_charge_current']:>8.2f} A")
        if "batt_charge_power" in rt:
            print(f"  Power:    {rt['batt_charge_power']:>8.2f} W")
        if "batt_soc" in rt:
            print(f"  SOC:      {rt['batt_soc']:>7d} %")
        if "charge_mode" in rt:
            print(f"  Mode:     {rt['charge_mode']:>12s}")
        if "batt_temp" in rt:
            print(f"  Temp:     {rt['batt_temp']:>8.2f} C")

        print(f"\n  --- Load ---")
        if "load_voltage" in rt:
            print(f"  Voltage:  {rt['load_voltage']:>8.2f} V")
        if "load_current" in rt:
            print(f"  Current:  {rt['load_current']:>8.2f} A")
        if "load_power" in rt:
            print(f"  Power:    {rt['load_power']:>8.2f} W")

        if "device_temp" in rt:
            print(f"\n  Device Temp: {rt['device_temp']:>5.2f} C")

    if energy:
        print(f"\n  --- Energy Generation ---")
        for key, label in [("gen_today", "Today"), ("gen_month", "Month"),
                           ("gen_year", "Year"), ("gen_total", "Total")]:
            if key in energy:
                print(f"  {label + ':':>8s}  {energy[key]:>8.2f} kWh")

        print(f"\n  --- Energy Consumption ---")
        for key, label in [("use_today", "Today"), ("use_month", "Month"),
                           ("use_year", "Year"), ("use_total", "Total")]:
            if key in energy:
                print(f"  {label + ':':>8s}  {energy[key]:>8.2f} kWh")

    if not rt and not energy:
        print("\n  No data received.")

    print("\n" + "=" * 55)


def scan_devices(timeout: int = 10):
    """Scan for BLE devices using BlueZ D-Bus API."""
    import dbus
    import dbus.mainloop.glib

    BLUEZ_SERVICE = "org.bluez"
    ADAPTER_IFACE = "org.bluez.Adapter1"
    DEVICE_IFACE = "org.bluez.Device1"
    PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
    OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

    print(f"Scanning for BLE devices ({timeout}s)...\n")

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    obj_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, "/"), OBJECT_MANAGER_IFACE
    )
    adapter_path = None
    for path, ifaces in obj_mgr.GetManagedObjects().items():
        if ADAPTER_IFACE in ifaces:
            adapter_path = path
            break

    if not adapter_path:
        print("No Bluetooth adapter found.")
        return

    adapter = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, adapter_path), ADAPTER_IFACE
    )
    adapter.SetDiscoveryFilter({"Transport": dbus.String("le")})

    try:
        adapter.StartDiscovery()
    except dbus.exceptions.DBusException:
        pass

    time.sleep(timeout)

    try:
        adapter.StopDiscovery()
    except dbus.exceptions.DBusException:
        pass

    devices = []
    for path, ifaces in obj_mgr.GetManagedObjects().items():
        if DEVICE_IFACE in ifaces:
            props = ifaces[DEVICE_IFACE]
            addr = str(props.get("Address", ""))
            name = str(props.get("Name", props.get("Alias", "")))
            if addr:
                devices.append((addr, name))

    if not devices:
        print("No devices found.")
        return

    print(f"{'Address':<20} {'Name'}")
    print("-" * 50)
    for addr, name in sorted(devices, key=lambda d: d[1]):
        marker = ""
        if any(kw in name.lower() for kw in ["epever", "tracer", "hn_", "fapao", "solar", "bt05"]):
            marker = "  <-- likely EPEver"
        print(f"{addr:<20} {name}{marker}")

    print(f"\nConnect with: python epever_ble.py --addr <address>")


def main():
    parser = argparse.ArgumentParser(description="EPEver Tracer CPN 7810 BLE Client")
    parser.add_argument("--scan", action="store_true",
                        help="Scan for nearby BLE devices")
    parser.add_argument("--addr", type=str,
                        help="BLE device address (XX:XX:XX:XX:XX:XX)")
    parser.add_argument("--addr-type", type=str, default="public",
                        choices=["public", "random"],
                        help="BLE address type (default: public)")
    parser.add_argument("--loop", action="store_true",
                        help="Continuously poll data")
    parser.add_argument("--interval", type=int, default=5,
                        help="Poll interval in seconds (default: 5)")
    parser.add_argument("--raw", type=str,
                        help="Send raw Modbus hex frame and print response")
    parser.add_argument("--slave", type=int, default=1,
                        help="Modbus slave ID (default: 1)")
    args = parser.parse_args()

    if args.scan:
        scan_devices()
        return

    if not args.addr:
        parser.error("--addr is required (or use --scan to find devices)")

    with L2capBLE(args.addr, args.addr_type) as ble:
        if not ble.connect():
            # Retry once
            time.sleep(2)
            ble.disconnect()
            if not ble.connect():
                print("Failed to connect. Is the device powered on and in range?")
                sys.exit(1)

        ble.enable_notifications()

        if args.raw:
            frame = bytes.fromhex(args.raw)
            print(f"TX: {frame.hex()}")
            response = ble.send_modbus(frame)
            if response:
                print(f"RX: {response.hex()}")
                if verify_modbus_crc(response):
                    print("CRC: OK")
                else:
                    print("CRC: INVALID (response may be truncated)")
            else:
                print("No response.")
            return

        try:
            while True:
                rt, energy = read_all_data(ble)
                display_data(rt, energy)

                if not args.loop:
                    break

                print(f"\nNext read in {args.interval}s... (Ctrl+C to stop)")
                time.sleep(args.interval)

        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
