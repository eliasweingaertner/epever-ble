#!/usr/bin/env python3
"""
EPEver Tracer CPN 7810 BLE Client

Connects to an EPEver charge controller via its built-in BLE interface,
reads Modbus registers over BLE, and displays real-time solar data.

Uses gatttool (from BlueZ) for BLE communication since the HN-series
BLE module disconnects during bleak's service discovery.

GATT layout (discovered from this device):
  Service 00002b00 (handles 0x000e-0x0028):
    Write char:  00002b14 val_handle=0x001e (WRITE_NO_RSP|NOTIFY)
    Notify char: 00002b10 val_handle=0x0010 (NOTIFY), CCCD=0x0011
    Notify char: 00002b16 val_handle=0x0026 (NOTIFY), CCCD=0x0027

  Modbus RTU frames are written to 0x001e, responses arrive as
  notifications on 0x0010 (and mirrored on 0x0026). Responses may
  be fragmented across multiple BLE notifications (20-byte default MTU).

Usage:
    python epever_ble.py --scan                    # Find nearby BLE devices
    python epever_ble.py --addr XX:XX:XX:XX:XX:XX  # Read all data once
    python epever_ble.py --addr XX:XX:XX --loop    # Poll continuously
    python epever_ble.py --addr XX:XX:XX --raw HEX # Send raw Modbus hex
"""

import argparse
import atexit
import os
import re
import signal
import struct
import subprocess
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


# --- BLE handles (from GATT discovery) ---

WRITE_HANDLE = "0x001e"       # Char 00002b14 value handle
NOTIFY_CCCD_1 = "0x0011"     # CCCD for char 00002b10 (notifications)
NOTIFY_CCCD_2 = "0x001f"     # CCCD for char 00002b14
NOTIFY_CCCD_3 = "0x0027"     # CCCD for char 00002b16
NOTIFY_HANDLE = 0x0010        # Where responses arrive

# --- EPEver Register Map ---

CHARGING_MODES = {0: "Not Charging", 1: "Float", 2: "Boost", 3: "Equalization"}
BATTERY_TYPES = {0: "User", 1: "Sealed", 2: "GEL", 3: "Flooded"}


class GatttoolBLE:
    """Drives gatttool interactive mode via subprocess."""

    def __init__(self, address: str):
        self.address = address
        self.proc: Optional[subprocess.Popen] = None
        self.connected = False

    def start(self):
        self.proc = subprocess.Popen(
            ["gatttool", "-b", self.address, "-I"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )

    def _send(self, cmd: str):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\[K', '', text)

    def _read_until(self, patterns: list[str], timeout: float = 10.0) -> str:
        """Read output until one of the patterns is found or timeout."""
        import select, os, fcntl
        # Make stdout non-blocking
        fd = self.proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        buf = ""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([self.proc.stdout], [], [], min(remaining, 0.2))
                if ready:
                    try:
                        chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    clean = self._strip_ansi(buf)
                    for pat in patterns:
                        if pat.lower() in clean.lower():
                            time.sleep(0.1)
                            # Drain remaining
                            try:
                                extra = os.read(fd, 4096).decode("utf-8", errors="replace")
                                buf += extra
                            except (BlockingIOError, OSError):
                                pass
                            return buf
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)  # restore blocking
        return buf

    def _collect_notifications(self, timeout: float = 2.0) -> list[bytes]:
        """Collect all notification data within timeout."""
        import select, os, fcntl
        fd = self.proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        notifications = []
        buf = ""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([self.proc.stdout], [], [], min(remaining, 0.2))
                if ready:
                    try:
                        chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    buf += chunk

                    # Parse notification lines from the buffer
                    clean = self._strip_ansi(buf)
                    while "\n" in clean:
                        line_end = clean.index("\n")
                        line = clean[:line_end]
                        clean = clean[line_end + 1:]
                        buf = clean  # keep unparsed remainder

                        match = re.search(
                            r"Notification handle = (0x[0-9a-fA-F]+) value: ([\s0-9a-fA-F]+)",
                            line,
                        )
                        if match:
                            handle = int(match.group(1), 16)
                            hex_str = match.group(2).strip()
                            data = bytes.fromhex(hex_str.replace(" ", ""))
                            if handle == NOTIFY_HANDLE:
                                notifications.append(data)
                                # Extend deadline to catch more fragments
                                deadline = time.monotonic() + 0.8
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)

        return notifications

    def connect(self) -> bool:
        print(f"Connecting to {self.address}...")
        self._send("connect")
        result = self._read_until(["successful", "Error", "error"], timeout=10.0)
        if "successful" in result:
            self.connected = True
            print("Connected.")
            return True
        print(f"Connection failed: {result.strip()}")
        return False

    def enable_notifications(self):
        """Enable notifications on the response characteristics."""
        for cccd in [NOTIFY_CCCD_1, NOTIFY_CCCD_2, NOTIFY_CCCD_3]:
            self._send(f"char-write-req {cccd} 0100")
            result = self._read_until(["written successfully", "Error"], timeout=3.0)
            if "Error" in result:
                print(f"  Warning: Could not enable CCCD {cccd}")
        time.sleep(0.2)

    def send_modbus(self, frame: bytes, timeout: float = 3.0) -> Optional[bytes]:
        """Send a Modbus RTU frame and collect the response."""
        # Drain any pending data
        self._collect_notifications(timeout=0.1)

        hex_str = frame.hex()
        self._send(f"char-write-cmd {WRITE_HANDLE} {hex_str}")
        time.sleep(0.1)

        # Collect notification fragments
        fragments = self._collect_notifications(timeout=timeout)
        if not fragments:
            return None

        # Assemble response from fragments
        # Each fragment is a chunk of the Modbus response
        # First fragment has the header, subsequent ones are continuations
        response = bytearray()
        for frag in fragments:
            response.extend(frag)

        return bytes(response)

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
            # Incomplete response (missing CRC bytes), but data may be valid
            pass

        registers = []
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                registers.append(struct.unpack('>H', data[i:i + 2])[0])
        return registers

    def disconnect(self):
        if self.proc:
            try:
                self._send("disconnect")
                time.sleep(0.3)
                self._send("quit")
                self.proc.wait(timeout=3)
            except Exception:
                pass
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            self.proc = None
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


def read_all_data(ble: GatttoolBLE) -> tuple[dict, dict]:
    rt = {}
    energy = {}
    delay = 0.3  # Delay between reads to avoid overwhelming the device

    # Split reads into smaller chunks to fit within BLE notification MTU.
    # Default MTU = 20 bytes payload. Modbus response overhead = 5 bytes
    # (slave + func + bytecount + CRC16), so ~7 registers per notification.
    # Reading 8 registers at a time is safe.

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
    """Scan for BLE devices using hcitool lescan + bluetoothctl."""
    print(f"Scanning for BLE devices ({timeout}s)...\n")
    try:
        result = subprocess.run(
            ["bluetoothctl", "--timeout", str(timeout), "scan", "on"],
            capture_output=True, text=True, timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        pass

    # List all known devices
    result = subprocess.run(
        ["bluetoothctl", "devices"],
        capture_output=True, text=True, timeout=5,
    )

    devices = []
    for line in result.stdout.splitlines():
        match = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line)
        if match:
            devices.append((match.group(1), match.group(2)))

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

    # Kill any stale gatttool processes for this device
    subprocess.run(
        ["pkill", "-f", f"gatttool.*{args.addr}"],
        capture_output=True, timeout=3,
    )
    time.sleep(1)

    with GatttoolBLE(args.addr) as ble:
        if not ble.connect():
            # Retry once
            time.sleep(2)
            ble.disconnect()
            ble.start()
            if not ble.connect():
                print("Failed to connect. Is the device powered on and in range?")
                sys.exit(1)

        ble.enable_notifications()

        if args.raw:
            # Send raw Modbus frame
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
