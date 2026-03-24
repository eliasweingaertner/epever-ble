"""CLI entry point for EPEver BLE.

Usage:
    python -m epever_ble --scan
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX --loop
    python -m epever_ble --addr XX:XX:XX:XX:XX:XX --raw HEX
"""

import argparse
import logging
import sys
import time

from .ble import verify_modbus_crc
from .reader import L2capBLE, read_all_data


def display_data(data: dict):
    print("\n" + "=" * 55)
    print("  EPEver Tracer CPN 7810 - Live Data")
    print("=" * 55)

    has_rt = any(
        k in data
        for k in ("pv_voltage", "batt_voltage", "load_voltage", "device_temp")
    )
    has_energy = any(k in data for k in ("gen_today", "use_today"))

    if has_rt:
        print(f"\n  --- Solar Panel (PV) ---")
        if "pv_voltage" in data:
            print(f"  Voltage:  {data['pv_voltage']:>8.2f} V")
        if "pv_current" in data:
            print(f"  Current:  {data['pv_current']:>8.2f} A")
        if "pv_power" in data:
            print(f"  Power:    {data['pv_power']:>8.2f} W")

        print(f"\n  --- Battery ---")
        if "batt_voltage" in data:
            print(f"  Voltage:  {data['batt_voltage']:>8.2f} V")
        if "batt_charge_current" in data:
            print(f"  Current:  {data['batt_charge_current']:>8.2f} A")
        if "batt_charge_power" in data:
            print(f"  Power:    {data['batt_charge_power']:>8.2f} W")
        if "batt_soc" in data:
            print(f"  SOC:      {data['batt_soc']:>7d} %")
        if "charge_mode" in data:
            print(f"  Mode:     {data['charge_mode']:>12s}")
        if "batt_temp" in data:
            print(f"  Temp:     {data['batt_temp']:>8.2f} C")

        print(f"\n  --- Load ---")
        if "load_voltage" in data:
            print(f"  Voltage:  {data['load_voltage']:>8.2f} V")
        if "load_current" in data:
            print(f"  Current:  {data['load_current']:>8.2f} A")
        if "load_power" in data:
            print(f"  Power:    {data['load_power']:>8.2f} W")

        if "device_temp" in data:
            print(f"\n  Device Temp: {data['device_temp']:>5.2f} C")

    if has_energy:
        print(f"\n  --- Energy Generation ---")
        for key, label in [("gen_today", "Today"), ("gen_month", "Month"),
                           ("gen_year", "Year"), ("gen_total", "Total")]:
            if key in data:
                print(f"  {label + ':':>8s}  {data[key]:>8.2f} kWh")

        print(f"\n  --- Energy Consumption ---")
        for key, label in [("use_today", "Today"), ("use_month", "Month"),
                           ("use_year", "Year"), ("use_total", "Total")]:
            if key in data:
                print(f"  {label + ':':>8s}  {data[key]:>8.2f} kWh")

    if not data:
        print("\n  No data received.")

    print("\n" + "=" * 55)


def scan_devices(timeout: int = 10):
    """Scan for BLE devices using BlueZ D-Bus API."""
    import dbus
    import dbus.mainloop.glib

    BLUEZ_SERVICE = "org.bluez"
    ADAPTER_IFACE = "org.bluez.Adapter1"
    DEVICE_IFACE = "org.bluez.Device1"
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

    print(f"\nConnect with: python -m epever_ble --addr <address>")


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
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")
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
