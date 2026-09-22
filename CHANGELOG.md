# Changelog

## 1.2.0 (2026-09-22)

- Add a Home Assistant switch for the controller's load output and a load mode sensor, and `--load on|off` to the CLI. Switching requires the controller's Manual load mode.
- Validate Modbus replies identically in both transports: CRC, slave ID, function code and register count.
- Negotiate the ATT MTU in the standalone CLI. At the default MTU, 8-register replies lost their last CRC byte.
- Ship icon and logo in `brand/` (Home Assistant 2026.3+) and exclude them from the MIT license.
- Add HACS metadata and automated HACS/hassfest validation.
- Add protocol and register-parsing tests.
- Expose the signed controller-side battery net current from registers `0x331B-0x331C`.
- Correct the G3 register semantics for PV output and battery output, and expose the previously omitted battery-output and MOSFET measurements.
- Document direct hardware validation with the EPEVER XTRA3210N G3.
- Derive the Home Assistant device name from the config entry instead of hardcoding a model or household-specific identity.
- Route Home Assistant connections through its Bluetooth manager, including connectable ESPHome Bluetooth proxies, while keeping raw L2CAP local to the standalone CLI.

## 1.0.0

- Initial standalone client and Home Assistant custom integration for EPEVER controllers with compatible built-in HN-series BLE modules.
