# epever-ble

A Python script to read data from EPEver Tracer charge controllers over Bluetooth Low Energy (BLE) — no RS-485 adapter or additional hardware required.

Tested on the **EPEver Tracer CPN 7810** with its built-in HN-series BLE module.

## What it reads

- **Solar panel**: voltage, current, power
- **Battery**: voltage, charge current, power, state of charge, temperature, charging mode
- **Load**: voltage, current, power
- **Device**: temperature
- **Energy statistics**: daily/monthly/yearly/total generation and consumption

```
=======================================================
  EPEver Tracer CPN 7810 - Live Data
=======================================================

  --- Solar Panel (PV) ---
  Voltage:     28.87 V
  Current:      2.07 A
  Power:       59.91 W

  --- Battery ---
  Voltage:     26.63 V
  Current:      2.16 A
  Power:       57.52 W
  SOC:           85 %
  Mode:        Boost
  Temp:        10.91 C

  --- Load ---
  Voltage:     26.63 V
  Current:      0.09 A
  Power:        2.39 W

  Device Temp: 20.67 C

  --- Energy Generation ---
    Today:      1.04 kWh
    Month:     10.19 kWh
     Year:      8.70 kWh
    Total:      4.84 kWh

  --- Energy Consumption ---
    Today:      0.03 kWh
    Month:      0.21 kWh
     Year:      2.69 kWh
    Total:      4.25 kWh

=======================================================
```

## Requirements

- Linux with BlueZ (`bluetoothctl`, `gatttool`)
- Python 3.10+
- The controller must be paired/bonded first (see below)

No Python dependencies beyond the standard library.

## Pairing

Before first use, pair your controller via `bluetoothctl`:

```bash
bluetoothctl
> scan on
# Wait for your device to appear (look for "HN_" prefix)
> scan off
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> quit
```

## Usage

```bash
# Scan for nearby BLE devices
python epever_ble.py --scan

# Read all data once
python epever_ble.py --addr XX:XX:XX:XX:XX:XX

# Continuous monitoring (default 5s interval)
python epever_ble.py --addr XX:XX:XX:XX:XX:XX --loop

# Custom poll interval
python epever_ble.py --addr XX:XX:XX:XX:XX:XX --loop --interval 10

# Send a raw Modbus RTU frame (hex) and print response
python epever_ble.py --addr XX:XX:XX:XX:XX:XX --raw 0104310000013f36
```

## How it works

The EPEver CPN's built-in BLE module exposes a GATT service that acts as a Modbus RTU bridge. Standard Modbus frames (with CRC16) are written to one characteristic and responses arrive as notifications on another.

**GATT layout:**

| Role | UUID | Handle | Properties |
|------|------|--------|------------|
| Write (TX) | `00002b14` | `0x001e` | Write Without Response, Notify |
| Notify (RX) | `00002b10` | `0x0010` | Notify |
| Notify (mirror) | `00002b16` | `0x0026` | Notify |

The Modbus register map is the standard EPEver Tracer map:

| Register | Description | Unit | Scale |
|----------|-------------|------|-------|
| `0x3100` | PV Voltage | V | /100 |
| `0x3101` | PV Current | A | /100 |
| `0x3102-03` | PV Power | W | /100 (32-bit) |
| `0x3104` | Battery Voltage | V | /100 |
| `0x3105` | Battery Charge Current | A | /100 |
| `0x310C` | Load Voltage | V | /100 |
| `0x310D` | Load Current | A | /100 |
| `0x3110` | Battery Temperature | C | /100 (signed) |
| `0x3111` | Device Temperature | C | /100 (signed) |
| `0x311A` | Battery SOC | % | |
| `0x3200` | Battery Status | | bitfield |
| `0x3201` | Charging Status | | bitfield |
| `0x330C-13` | Generated Energy (day/month/year/total) | kWh | /100 (32-bit) |
| `0x3304-0B` | Consumed Energy (day/month/year/total) | kWh | /100 (32-bit) |

## Known limitations

- Uses `gatttool` (deprecated but widely available) because the HN-series BLE module disconnects during `bleak`'s service discovery. A future version could use raw D-Bus calls instead.
- BLE default MTU is 20 bytes, so responses for large register reads arrive fragmented. The script works around this by reading in small batches (8 registers at a time).
- Only tested on the CPN 7810. Other EPEver Tracer models with built-in BLE (HN_ prefix in device name) likely work too since they share the same Modbus register map. Models using external BLE dongles (eBox-BLE-01) may use different GATT UUIDs (typically FFE0/FFE1).

## Background

This project was born out of frustration: the EPEver Tracer CPN 7810 has a perfectly good built-in Bluetooth interface, but the only way to use it is through EPEver's proprietary "Solar Guardian" Android app. There is no open-source library, no protocol documentation, and no way to log data to your own system.

The protocol was reverse-engineered in a single session by:

1. **Capturing a Bluetooth HCI snoop log** from Android while using the Solar Guardian app. Android has a developer option to log all Bluetooth traffic to a file.
2. **Parsing the btsnoop log** to extract ATT/GATT packets, identifying two separate BLE connections and the data exchange patterns.
3. **Discovering the GATT services** using `gatttool --primary` and `--characteristics` (since `bleak` couldn't maintain a connection long enough for service discovery).
4. **Identifying the Modbus register map** from the [epevermodbus](https://github.com/rosswarren/epevermodbus) Python library, which documents the full register map for EPEver Tracer controllers over RS-485. The registers are identical regardless of transport.
5. **Confirming the protocol** by writing a Modbus RTU frame to the write characteristic and receiving a valid response via notifications.

The entire reverse-engineering and implementation was done with [Claude Code](https://claude.ai/claude-code).

## Resources

These resources were used during development:

- **[epevermodbus](https://github.com/rosswarren/epevermodbus)** — Python library for EPEver Tracer controllers over RS-485. Provided the complete Modbus register map.
- **[Android Bluetooth HCI snoop log](https://developer.android.com/develop/connectivity/bluetooth/ble/ble-overview)** — Android's developer option to capture BLE traffic was essential for reverse-engineering the GATT protocol.
- **[Modbus RTU specification](https://modbus.org/specs.php)** — The framing, function codes, and CRC16 algorithm.
- **[Bluetooth GATT specification](https://www.bluetooth.com/specifications/specs/core-specification/)** — For understanding ATT handles, CCCDs, notifications, and service discovery.
- **BlueZ `gatttool`** — The workhorse for BLE communication on Linux when higher-level libraries can't maintain a connection.

## License

MIT
