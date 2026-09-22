# epever-ble

A Python tool and Home Assistant integration to read data from compatible EPEVER solar charge controllers over Bluetooth Low Energy (BLE) — no RS-485 adapter or additional hardware required.

## Hardware compatibility

Compatibility depends on the controller's BLE GATT handle layout, not only on the Modbus register map.

| Controller | BLE module/name | Evidence |
|---|---|---|
| EPEVER Tracer CPN 7810 | Built-in HN-series BLE | Directly tested by the original author |
| EPEVER XTRA3210N G3 | Built-in `HN_` BLE | Directly tested in Home Assistant 2026.8.3 through local Bluetooth and an ESPHome Bluetooth proxy |
| EPEVER Tracer AN2206 | Not recorded | [Community report](https://community.home-assistant.io/t/monitor-epever-tracer-charge-controllers-via-built-in-ble-dongle/998646) |

Other EPEVER controllers are not implied compatible. Models using an external eBox-BLE-01 may expose a different GATT layout.

## What it reads

- **Solar panel**: voltage, current, power
- **Battery**: voltage, charge/output/net current, charge/output power, state of charge, remote temperature, charging mode
- **Load**: voltage, current, power
- **Device**: equipment and MOSFET temperature
- **Energy statistics**: daily/monthly/yearly/total generation and consumption

```
=======================================================
  EPEVER Tracer CPN 7810 - Live Data
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

## Standalone CLI requirements

- Linux with BlueZ 5.x
- Python 3.10+
- No Python dependencies beyond the standard library
- `bluetoothctl` (included with BlueZ) is used for device scanning

## Standalone pairing

Pairing may not be necessary. If the standalone CLI cannot connect, try pairing manually via `bluetoothctl`:

```bash
bluetoothctl
> scan on
# Wait for your device to appear (look for "HN_" prefix)
> scan off
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> quit
```

## Standalone CLI

```bash
# Install the package
pip install -e .

# Scan for nearby BLE devices
python -m epever_ble --scan

# Read all data once
python -m epever_ble --addr XX:XX:XX:XX:XX:XX

# Continuous monitoring (default 5s interval)
python -m epever_ble --addr XX:XX:XX:XX:XX:XX --loop

# Custom poll interval
python -m epever_ble --addr XX:XX:XX:XX:XX:XX --loop --interval 10

# Send a raw Modbus RTU frame (hex) and print response
python -m epever_ble --addr XX:XX:XX:XX:XX:XX --raw 0104310000013f36

# Enable debug logging
python -m epever_ble --addr XX:XX:XX:XX:XX:XX -v
```

## Home Assistant integration

A custom integration that exposes all charge controller data as Home Assistant sensor entities.

The integration uses Home Assistant's Bluetooth manager. Home Assistant may choose a local adapter or any connectable remote adapter, including an ESPHome Bluetooth proxy; the integration does not open a local Linux Bluetooth socket or fall back around that choice.

### Installation with HACS

Until the repository is included in the default HACS catalog, add it as a custom repository:

1. Open **HACS > Integrations**.
2. Open the three-dot menu and select **Custom repositories**.
3. Add `https://github.com/eliasweingaertner/epever-ble` with category **Integration**.
4. Install **EPEVER BLE** and restart Home Assistant.
5. Go to **Settings > Devices & Services > Add Integration** and search for **EPEVER BLE**.
6. Select the controller from the discovered devices, or enter its MAC address manually.

### Manual installation

1. Copy the `custom_components/epever_ble` directory into your Home Assistant `config/custom_components/` directory:

   ```bash
   cp -r custom_components/epever_ble /path/to/homeassistant/config/custom_components/
   ```

2. Restart Home Assistant.

3. Go to **Settings > Devices & Services > Add Integration** and search for **EPEVER BLE**.

4. Select your charge controller from the discovered devices, or enter the MAC address manually.

If the integration cannot find the controller, verify that Home Assistant has a working local Bluetooth adapter or a connectable Bluetooth proxy with a free connection slot. Pairing and raw-socket capabilities are not required by the Home Assistant integration.

### Entities

The integration creates a device with the following sensor entities:

| Entity | Unit | Description |
|--------|------|-------------|
| PV Voltage | V | Solar panel voltage |
| PV Current | A | Solar panel current |
| PV Power | W | Solar panel power |
| PV Output Voltage | V | Charging-branch voltage at the battery-side PV output |
| Battery Voltage | V | Battery voltage |
| Battery Charge Current | A | Battery charge current |
| Battery Output Current | A | Battery output current reported by the controller |
| Battery Output Power | W | Battery output power reported by the controller |
| Battery Net Current | A | Controller-side net current; positive is charging and negative is discharging |
| Battery Charge Power | W | Battery charge power |
| Battery State of Charge | % | Battery SOC |
| Remote Battery Temperature | °C | Optional remote battery-temperature input |
| Charging Mode | | Not Charging / Float / Boost / Equalization |
| Load Voltage | V | Load output voltage |
| Load Current | A | Load output current |
| Load Power | W | Load output power |
| Device Temperature | °C | Controller internal temperature |
| MOSFET Temperature | °C | Controller MOSFET temperature |
| Energy Generated Today | kWh | Daily solar generation |
| Energy Generated This Month | kWh | Monthly solar generation |
| Energy Generated This Year | kWh | Yearly solar generation |
| Total Energy Generated | kWh | Lifetime solar generation |
| Energy Consumed Today | kWh | Daily load consumption |
| Energy Consumed This Month | kWh | Monthly load consumption |
| Energy Consumed This Year | kWh | Yearly load consumption |
| Total Energy Consumed | kWh | Lifetime load consumption |

Energy sensors use `total_increasing` state class, making them compatible with Home Assistant's energy dashboard.

## How it works

The compatible controllers' built-in BLE module exposes a GATT service that acts as a Modbus RTU bridge. Standard Modbus frames (with CRC16) are written to one characteristic and responses arrive as notifications on another.

The Home Assistant integration resolves the controller through Home Assistant's Bluetooth manager and uses Bleak GATT characteristics by UUID. This keeps adapter selection, connection slots, and ESPHome Bluetooth proxy routing under Home Assistant's control.

The standalone CLI has no Home Assistant dependency. It retains the original raw L2CAP implementation and speaks ATT directly through the host's local Linux Bluetooth adapter.

**GATT layout:**

| Role | UUID | Raw ATT value handle | Properties |
|------|------|----------------------|------------|
| Write (TX) | `00002b14` | `0x001e` | Write Without Response, Notify |
| Notify (RX) | `00002b10` | `0x0010` | Notify |
| Notify (mirror) | `00002b16` | `0x0026` | Notify |

The Home Assistant transport resolves these by UUID. Raw ATT value handles are used only by the standalone CLI and are not interchangeable with Bleak characteristic handles.

The Modbus register map is the standard EPEVER Tracer map:

| Register | Description | Unit | Scale |
|----------|-------------|------|-------|
| `0x3100` | PV Voltage | V | /100 |
| `0x3101` | PV Current | A | /100 |
| `0x3102-03` | PV Power | W | /100 (32-bit) |
| `0x3104` | PV Output Voltage | V | /100 |
| `0x3105` | PV Output / Battery Charge Current | A | /100 |
| `0x3106-07` | PV Output / Battery Charge Power | W | /100 (32-bit) |
| `0x3108` | Battery Voltage | V | /100 |
| `0x3109` | Battery Output Current | A | /100 |
| `0x310A-0B` | Battery Output Power | W | /100 (32-bit) |
| `0x331B-1C` | Battery Net Current | A | /100 (signed 32-bit) |
| `0x310C` | Load Voltage | V | /100 |
| `0x310D` | Load Current | A | /100 |
| `0x3110` | Remote Battery Temperature | C | /100 (signed) |
| `0x3111` | Device Temperature | C | /100 (signed) |
| `0x3112` | MOSFET Temperature | C | /100 on the validated XTRA3210N G3 |
| `0x311A` | Battery SOC | % | |
| `0x3200` | Battery Status | | bitfield |
| `0x3201` | Charging Status | | bitfield |
| `0x330C-13` | Generated Energy (day/month/year/total) | kWh | /100 (32-bit) |
| `0x3304-0B` | Consumed Energy (day/month/year/total) | kWh | /100 (32-bit) |

## Known limitations

- The standalone CLI is Linux-only because it uses Linux-specific L2CAP Bluetooth sockets. The Home Assistant integration uses Home Assistant's cross-adapter Bluetooth API instead.
- At the default ATT MTU of 23, a notification carries at most 20 bytes, one byte short of an 8-register reply (21 bytes); the controller's BLE bridge then drops the last CRC byte. The standalone CLI therefore negotiates a larger MTU right after connecting (Home Assistant's Bluetooth stack does this by itself). Registers are still read in batches of at most 8.
- The built-in `HN_` profile was directly validated on an XTRA3210N G3. Other models must be tested individually; sharing the EPEVER Modbus register map does not prove BLE profile compatibility. Models using external BLE dongles (eBox-BLE-01) may use different GATT UUIDs (typically FFE0/FFE1).
- Battery Net Current is the controller-side value reported by registers `0x331B-0x331C`. External chargers connected directly to a shared battery bus bypass the controller's charging input; this entity must not be treated as a confirmed whole-bus shunt measurement.
- Device names and controller models are not hardcoded in the integration. The config entry and Home Assistant device registry own the user-visible identity.

## Background

This project was born out of frustration: the EPEVER Tracer CPN 7810 has a perfectly good built-in Bluetooth interface, but the only way to use it is through EPEVER's proprietary "Solar Guardian" Android app. There is no open-source library, no protocol documentation, and no way to log data to your own system.

The protocol was reverse-engineered in a single session by:

1. **Capturing a Bluetooth HCI snoop log** from Android while using the Solar Guardian app. Android has a developer option to log all Bluetooth traffic to a file.
2. **Parsing the btsnoop log** to extract ATT/GATT packets, identifying two separate BLE connections and the data exchange patterns.
3. **Discovering the GATT services** using `gatttool --primary` and `--characteristics` to map out the GATT service/characteristic layout.
4. **Identifying the Modbus register map** from the [epevermodbus](https://github.com/rosswarren/epevermodbus) Python library, which documents the full register map for EPEVER Tracer controllers over RS-485. The registers are identical regardless of transport.
5. **Confirming the protocol** by writing a Modbus RTU frame to the write characteristic and receiving a valid response via notifications.

The entire reverse-engineering and implementation was done with [Claude Code](https://claude.ai/claude-code).

## Resources

These resources were used during development:

- **[epevermodbus](https://github.com/rosswarren/epevermodbus)** — Python library for EPEVER Tracer controllers over RS-485. Provided the complete Modbus register map.
- **[Android Bluetooth HCI snoop log](https://developer.android.com/develop/connectivity/bluetooth/ble/ble-overview)** — Android's developer option to capture BLE traffic was essential for reverse-engineering the GATT protocol.
- **[Modbus RTU specification](https://modbus.org/specs.php)** — The framing, function codes, and CRC16 algorithm.
- **[Bluetooth GATT specification](https://www.bluetooth.com/specifications/specs/core-specification/)** — For understanding ATT handles, CCCDs, notifications, and service discovery.
- **Linux L2CAP / ATT sockets** — The standalone CLI opens a raw L2CAP SEQPACKET socket on CID 4 (ATT). The syscall sequence was determined by `strace`-ing `gatttool`.

## Trademarks

EPEVER is a trademark of its respective owner. This project is not affiliated with,
authorised by, or endorsed by EPEVER. The name is used only to identify the hardware this
software is compatible with.

The integration ships its icon and logo in `custom_components/epever_ble/brand/`, which
Home Assistant 2026.3 and later picks up automatically. These images are excluded from the
MIT license; see [LICENSE](LICENSE).

## License

MIT for the source code. The brand images are excluded; see [LICENSE](LICENSE).
