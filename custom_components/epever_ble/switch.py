"""Switch platform for EPEVER BLE integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EPEVERBLECoordinator

LOAD_SWITCH = SwitchEntityDescription(
    key="load_output",
    translation_key="load_output",
    name="Load Output",
    icon="mdi:power-plug",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EPEVERBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [EPEVERLoadSwitch(coordinator, entry.data[CONF_MAC], entry.title)]
    )


class EPEVERLoadSwitch(CoordinatorEntity[EPEVERBLECoordinator], SwitchEntity):
    """The controller's load output, switchable in manual load mode."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EPEVERBLECoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = LOAD_SWITCH
        self._attr_unique_id = f"{mac}_{LOAD_SWITCH.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=device_name,
            manufacturer="EPEVER",
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and "load_on" in self.coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("load_on")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_load(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_load(False)
