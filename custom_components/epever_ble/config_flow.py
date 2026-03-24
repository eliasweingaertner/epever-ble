"""Config flow for EPEver BLE integration."""

import asyncio
import logging
import re

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_MAC, CONF_SCAN_INTERVAL

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# Keywords that suggest an EPEver BLE device
EPEVER_KEYWORDS = ("hn_", "epever", "tracer", "fapao", "solar", "bt05")


async def _scan_bluetoothctl() -> dict[str, str]:
    """Get known BLE devices from bluetoothctl.

    Returns {mac: name} dict.
    """
    devices: dict[str, str] = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "devices",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        for line in stdout.decode(errors="replace").splitlines():
            # Format: "Device AA:BB:CC:DD:EE:FF DeviceName"
            parts = line.split(maxsplit=2)
            if len(parts) >= 3 and parts[0] == "Device" and MAC_REGEX.match(parts[1]):
                devices[parts[1].upper()] = parts[2]
            elif len(parts) == 2 and parts[0] == "Device" and MAC_REGEX.match(parts[1]):
                devices[parts[1].upper()] = parts[1]
    except (FileNotFoundError, asyncio.TimeoutError, OSError) as err:
        _LOGGER.debug("bluetoothctl scan failed: %s", err)
    return devices


def _is_likely_epever(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in EPEVER_KEYWORDS)


class EPEverBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EPEver BLE."""

    VERSION = 1

    def __init__(self):
        self._discovered_devices: dict[str, str] = {}

    async def async_step_user(self, user_input=None):
        """Step 1: scan for devices and let the user pick one."""
        if user_input is not None:
            mac = user_input[CONF_MAC]

            if mac == "__manual__":
                return await self.async_step_manual()

            mac = mac.upper()
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()

            name = self._discovered_devices.get(mac, mac[-8:])
            return self.async_create_entry(
                title=f"EPEver {name}",
                data={
                    CONF_MAC: mac,
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                },
            )

        # Scan for devices
        self._discovered_devices = await _scan_bluetoothctl()

        if not self._discovered_devices:
            # No devices found — go straight to manual entry
            return await self.async_step_manual()

        # Build selection list: "MAC — Name" for display
        device_options: dict[str, str] = {}
        for mac, name in self._discovered_devices.items():
            label = f"{name} ({mac})" if name != mac else mac
            device_options[mac] = label
        device_options["__manual__"] = "Enter MAC address manually..."

        # Pre-select the first likely EPEver device
        default_mac = None
        for mac, name in self._discovered_devices.items():
            if _is_likely_epever(name):
                default_mac = mac
                break
        if default_mac is None:
            default_mac = next(iter(self._discovered_devices))

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC, default=default_mac): vol.In(
                        device_options
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): vol.All(int, vol.Range(min=10)),
                }
            ),
        )

    async def async_step_manual(self, user_input=None):
        """Fallback step: manual MAC address entry."""
        errors = {}

        if user_input is not None:
            mac = user_input[CONF_MAC].strip().upper()

            if not MAC_REGEX.match(mac):
                errors["mac"] = "invalid_mac"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"EPEver {mac[-8:]}",
                    data={
                        CONF_MAC: mac,
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): str,
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): vol.All(int, vol.Range(min=10)),
                }
            ),
            errors=errors,
        )
