"""CLI entry point for EPEVER BLE.

Usage:
    python -m epever_ble --scan
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX --loop
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX --raw HEX
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX --load on|off
"""

import argparse
import logging
import sys
import time

from . import (
    LOAD_CONTROL_COIL,
    LOAD_MODE_MANUAL,
    LOAD_MODE_REGISTER,
    LOAD_MODES,
    L2capBLE,
    read_all_data,
    verify_modbus_crc,
)


def display_data(data: dict):
    print("\n" + "=" * 55)
    print("  EPEVER BLE Solar Charge Controller - Live Data")
    print("=" * 55)

    has_rt = any(
        k in data
        for k in (
            "pv_voltage",
            "pv_output_voltage",
            "batt_voltage",
            "batt_charge_current",
            "batt_output_current",
            "batt_net_current",
            "load_voltage",
            "load_mode",
            "load_on",
            "device_temp",
            "mosfet_temp",
        )
    )
    has_energy = any(k in data for k in ("gen_today", "use_today"))

    if has_rt:
        print("\n  --- Solar Panel (PV) ---")
        if "pv_voltage" in data:
            print(f"  Voltage:  {data['pv_voltage']:>8.2f} V")
        if "pv_current" in data:
            print(f"  Current:  {data['pv_current']:>8.2f} A")
        if "pv_power" in data:
            print(f"  Power:    {data['pv_power']:>8.2f} W")
        if "pv_output_voltage" in data:
            print(f"  Output Voltage: {data['pv_output_voltage']:>6.2f} V")

        print("\n  --- Battery ---")
        if "batt_voltage" in data:
            print(f"  Voltage:  {data['batt_voltage']:>8.2f} V")
        if "batt_charge_current" in data:
            print(f"  Charge Current: {data['batt_charge_current']:>5.2f} A")
        if "batt_output_current" in data:
            print(f"  Output Current: {data['batt_output_current']:>5.2f} A")
        if "batt_net_current" in data:
            print(f"  Net Current: {data['batt_net_current']:>8.2f} A")
        if "batt_charge_power" in data:
            print(f"  Charge Power: {data['batt_charge_power']:>7.2f} W")
        if "batt_output_power" in data:
            print(f"  Output Power: {data['batt_output_power']:>7.2f} W")
        if "batt_soc" in data:
            print(f"  SOC:      {data['batt_soc']:>7d} %")
        if "charge_mode" in data:
            print(f"  Mode:     {data['charge_mode']:>12s}")
        if "batt_temp" in data:
            print(f"  Remote Temp: {data['batt_temp']:>6.2f} C")

        print("\n  --- Load ---")
        if "load_voltage" in data:
            print(f"  Voltage:  {data['load_voltage']:>8.2f} V")
        if "load_current" in data:
            print(f"  Current:  {data['load_current']:>8.2f} A")
        if "load_power" in data:
            print(f"  Power:    {data['load_power']:>8.2f} W")
        if "load_mode" in data:
            print(f"  Mode:     {data['load_mode']:>12s}")
        if "load_on" in data:
            print(f"  Output:   {'ON' if data['load_on'] else 'OFF':>12s}")

        if "device_temp" in data:
            print(f"\n  Device Temp: {data['device_temp']:>5.2f} C")
        if "mosfet_temp" in data:
            print(f"  MOSFET Temp: {data['mosfet_temp']:>5.2f} C")

    if has_energy:
        print("\n  --- Energy Generation ---")
        for key, label in [
            ("gen_today", "Today"),
            ("gen_month", "Month"),
            ("gen_year", "Year"),
            ("gen_total", "Total"),
        ]:
            if key in data:
                print(f"  {label + ':':>8s}  {data[key]:>8.2f} kWh")

        print("\n  --- Energy Consumption ---")
        for key, label in [
            ("use_today", "Today"),
            ("use_month", "Month"),
            ("use_year", "Year"),
            ("use_total", "Total"),
        ]:
            if key in data:
                print(f"  {label + ':':>8s}  {data[key]:>8.2f} kWh")

    if not data:
        print("\n  No data received.")

    print("\n" + "=" * 55)


def switch_load(ble, on: bool, slave: int = 1) -> bool:
    """Switch the load output; return True once the new state is confirmed."""
    mode = ble.read_holding_registers(LOAD_MODE_REGISTER, 1, slave)
    if mode is None:
        print("Warning: could not read the load mode; trying anyway.")
    elif mode[0] != LOAD_MODE_MANUAL:
        name = LOAD_MODES.get(mode[0], f"Unknown({mode[0]})")
        print(
            f"Load output is in '{name}' mode; switching requires "
            f"'{LOAD_MODES[LOAD_MODE_MANUAL]}' mode. Nothing changed."
        )
        return False

    if not ble.write_coil(LOAD_CONTROL_COIL, on, slave):
        print("The controller did not confirm the command.")
        return False

    time.sleep(0.3)
    state = ble.read_coils(LOAD_CONTROL_COIL, 1, slave)
    if state is None:
        print("Command confirmed, but the new state could not be read back.")
        return False
    print(f"Load output is now {'ON' if state[0] else 'OFF'}.")
    return state[0] == on


def scan_devices(timeout: int = 10):
    """Scan for BLE devices using bluetoothctl."""
    import subprocess

    print(f"Scanning for BLE devices ({timeout}s)...\n")

    # Start a BLE scan, wait, then stop
    try:
        subprocess.run(
            ["bluetoothctl", "scan", "on"],
            timeout=timeout,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass  # Expected — scan runs until timeout
    except FileNotFoundError:
        print("bluetoothctl not found. Install BlueZ.")
        return

    subprocess.run(["bluetoothctl", "scan", "off"], capture_output=True, timeout=3)

    # List discovered devices
    result = subprocess.run(
        ["bluetoothctl", "devices"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    devices = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 3 and parts[0] == "Device":
            devices.append((parts[1], parts[2]))
        elif len(parts) == 2 and parts[0] == "Device":
            devices.append((parts[1], parts[1]))

    if not devices:
        print("No devices found.")
        return

    print(f"{'Address':<20} {'Name'}")
    print("-" * 50)
    for addr, name in sorted(devices, key=lambda d: d[1]):
        marker = ""
        if any(
            kw in name.lower()
            for kw in ["epever", "tracer", "hn_", "fapao", "solar", "bt05"]
        ):
            marker = "  <-- likely EPEVER"
        print(f"{addr:<20} {name}{marker}")

    print("\nConnect with: python -m epever_ble --addr <address>")


def main():
    parser = argparse.ArgumentParser(
        description="EPEVER BLE Solar Charge Controller Client"
    )
    parser.add_argument(
        "--scan", action="store_true", help="Scan for nearby BLE devices"
    )
    parser.add_argument(
        "--addr", type=str, help="BLE device address (XX:XX:XX:XX:XX:XX)"
    )
    parser.add_argument(
        "--addr-type",
        type=str,
        default="public",
        choices=["public", "random"],
        help="BLE address type (default: public)",
    )
    parser.add_argument("--loop", action="store_true", help="Continuously poll data")
    parser.add_argument(
        "--interval", type=int, default=5, help="Poll interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--raw", type=str, help="Send raw Modbus hex frame and print response"
    )
    parser.add_argument(
        "--load",
        choices=["on", "off"],
        help="Switch the load output (controller must be in manual load mode)",
    )
    parser.add_argument(
        "--slave", type=int, default=1, help="Modbus slave ID (default: 1)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    if args.scan:
        scan_devices()
        return

    if not args.addr:
        parser.error("--addr is required (or use --scan to find devices)")

    with L2capBLE(args.addr, args.addr_type) as ble:
        print(f"Connecting to {args.addr}...")
        if not ble.connect():
            time.sleep(2)
            ble.disconnect()
            if not ble.connect():
                print("Failed to connect. Is the device powered on and in range?")
                sys.exit(1)
        print("Connected.")

        ble.enable_notifications()

        if args.load:
            sys.exit(0 if switch_load(ble, args.load == "on", args.slave) else 1)

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
                data = read_all_data(ble)
                display_data(data)

                if not args.loop:
                    break

                print(f"\nNext read in {args.interval}s... (Ctrl+C to stop)")
                time.sleep(args.interval)

        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
