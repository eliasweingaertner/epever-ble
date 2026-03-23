"""Sensor platform for EPEver BLE integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EPEverBLECoordinator


@dataclass(frozen=True, kw_only=True)
class EPEverSensorDescription(SensorEntityDescription):
    """Describe an EPEver BLE sensor."""


SENSOR_DESCRIPTIONS: tuple[EPEverSensorDescription, ...] = (
    # --- PV ---
    EPEverSensorDescription(
        key="pv_voltage",
        translation_key="pv_voltage",
        name="PV Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="pv_current",
        translation_key="pv_current",
        name="PV Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="pv_power",
        translation_key="pv_power",
        name="PV Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    # --- Battery ---
    EPEverSensorDescription(
        key="batt_voltage",
        translation_key="batt_voltage",
        name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="batt_charge_current",
        translation_key="batt_charge_current",
        name="Battery Charge Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="batt_charge_power",
        translation_key="batt_charge_power",
        name="Battery Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="batt_soc",
        translation_key="batt_soc",
        name="Battery State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EPEverSensorDescription(
        key="batt_temp",
        translation_key="batt_temp",
        name="Battery Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    EPEverSensorDescription(
        key="charge_mode",
        translation_key="charge_mode",
        name="Charging Mode",
        icon="mdi:battery-charging",
    ),
    # --- Load ---
    EPEverSensorDescription(
        key="load_voltage",
        translation_key="load_voltage",
        name="Load Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="load_current",
        translation_key="load_current",
        name="Load Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="load_power",
        translation_key="load_power",
        name="Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    # --- Device ---
    EPEverSensorDescription(
        key="device_temp",
        translation_key="device_temp",
        name="Device Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # --- Energy Generation ---
    EPEverSensorDescription(
        key="gen_today",
        translation_key="gen_today",
        name="Energy Generated Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="gen_month",
        translation_key="gen_month",
        name="Energy Generated This Month",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="gen_year",
        translation_key="gen_year",
        name="Energy Generated This Year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="gen_total",
        translation_key="gen_total",
        name="Total Energy Generated",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    # --- Energy Consumption ---
    EPEverSensorDescription(
        key="use_today",
        translation_key="use_today",
        name="Energy Consumed Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="use_month",
        translation_key="use_month",
        name="Energy Consumed This Month",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="use_year",
        translation_key="use_year",
        name="Energy Consumed This Year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    EPEverSensorDescription(
        key="use_total",
        translation_key="use_total",
        name="Total Energy Consumed",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EPEverBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC]

    async_add_entities(
        EPEverSensor(coordinator, description, mac)
        for description in SENSOR_DESCRIPTIONS
    )


class EPEverSensor(CoordinatorEntity[EPEverBLECoordinator], SensorEntity):
    """A sensor entity for one EPEver data point."""

    _attr_has_entity_name = True
    entity_description: EPEverSensorDescription

    def __init__(
        self,
        coordinator: EPEverBLECoordinator,
        description: EPEverSensorDescription,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=f"EPEver {mac[-8:]}",
            manufacturer="EPEver",
            model="Tracer CPN",
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.entity_description.key in self.coordinator.data
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.key)
