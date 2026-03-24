"""Config flow for EPEver BLE integration."""

import re

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_MAC, CONF_SCAN_INTERVAL

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class EPEverBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EPEver BLE."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            mac = user_input[CONF_MAC].upper()

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
            step_id="user",
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
