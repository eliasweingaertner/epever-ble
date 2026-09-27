"""Register reading and data parsing for EPEVER charge controllers."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ble import L2capBLE
    from .ha_ble import HomeAssistantBLE

CHARGING_MODES = {0: "Not Charging", 1: "Float", 2: "Boost", 3: "Equalization"}

# Load output control (EPEVER Modbus protocol V2.3).
LOAD_CONTROL_COIL = 0x0002  # manual load on/off, honoured only in manual mode
LOAD_MODE_REGISTER = 0x903D  # holding register: load controlling mode
LOAD_MODE_MANUAL = 0
LOAD_MODES = {
    0: "Manual",
    1: "Light On/Off",
    2: "Light On + Timer",
    3: "Time Control",
}
REGISTER_BATCHES = (
    (0x3100, 8, "pv_battery"),
    (0x3108, 4, "battery_output"),
    (0x310C, 8, "load_temperature"),
    (0x311A, 2, "soc"),
    (0x3200, 3, "status"),
    (0x331B, 2, "net_battery_current"),
    (0x330C, 8, "generated_energy"),
    (0x3304, 8, "consumed_energy"),
)
READ_DELAY = 0.3

# Controller settings (holding registers, EPEVER Modbus protocol V2.3).
# They change rarely, so they are read separately from the measurements.
SETTINGS_BATCHES = (
    (0x9000, 15, "battery_settings"),
    (0x906B, 2, "charge_durations"),
    (0x9013, 3, "clock"),
)
BATTERY_TYPES = {0: "User", 1: "Sealed", 2: "GEL", 3: "Flooded"}
_SETTING_VOLTAGES = (  # (offset in the 0x9000 block, key)
    (3, "set_overvoltage_disconnect"),
    (4, "set_charging_limit"),
    (5, "set_overvoltage_reconnect"),
    (6, "set_equalize"),
    (7, "set_boost"),
    (8, "set_float"),
    (9, "set_boost_reconnect"),
    (10, "set_low_voltage_reconnect"),
    (11, "set_undervoltage_warning_reconnect"),
    (12, "set_undervoltage_warning"),
    (13, "set_low_voltage_disconnect"),
    (14, "set_discharging_limit"),
)


def _combine_32bit(low: int, high: int) -> float:
    return (high * 65536 + low) / 100.0


def _combine_signed_32bit(low: int, high: int) -> float:
    value = high * 65536 + low
    if value > 0x7FFFFFFF:
        value -= 0x100000000
    return value / 100.0


def _signed_temp(val: int) -> float:
    if val > 32767:
        val -= 65536
    return val / 100.0


def _merge_register_batch(data: dict, batch: str, registers: list[int] | None) -> None:
    """Merge one register batch into the flat sensor data mapping."""
    if not registers:
        return

    if batch == "pv_battery":
        if len(registers) > 0:
            data["pv_voltage"] = registers[0] / 100.0
        if len(registers) > 1:
            data["pv_current"] = registers[1] / 100.0
        if len(registers) > 3:
            data["pv_power"] = _combine_32bit(registers[2], registers[3])
        if len(registers) > 4:
            data["pv_output_voltage"] = registers[4] / 100.0
        if len(registers) > 5:
            data["batt_charge_current"] = registers[5] / 100.0
        if len(registers) > 7:
            data["batt_charge_power"] = _combine_32bit(registers[6], registers[7])
    elif batch == "battery_output":
        if len(registers) > 0:
            data["batt_voltage"] = registers[0] / 100.0
        if len(registers) > 1:
            data["batt_output_current"] = registers[1] / 100.0
        if len(registers) > 3:
            data["batt_output_power"] = _combine_32bit(registers[2], registers[3])
    elif batch == "load_temperature":
        if len(registers) > 0:
            data["load_voltage"] = registers[0] / 100.0
        if len(registers) > 1:
            data["load_current"] = registers[1] / 100.0
        if len(registers) > 3:
            data["load_power"] = _combine_32bit(registers[2], registers[3])
        if len(registers) > 4:
            data["batt_temp"] = _signed_temp(registers[4])
        if len(registers) > 5:
            data["device_temp"] = _signed_temp(registers[5])
        if len(registers) > 6:
            # XTRA3210N G3 reports centidegrees here despite the G3 protocol
            # table listing a coefficient of 1 for register 0x3112.
            data["mosfet_temp"] = registers[6] / 100.0
    elif batch == "soc":
        data["batt_soc"] = registers[0]
    elif batch == "status" and len(registers) >= 2:
        charge_mode = (registers[1] >> 2) & 0x03
        data["charge_mode"] = CHARGING_MODES.get(charge_mode, f"Unknown({charge_mode})")
    elif batch == "net_battery_current" and len(registers) >= 2:
        data["batt_net_current"] = _combine_signed_32bit(registers[0], registers[1])
    elif batch == "generated_energy" and len(registers) >= 8:
        data["gen_today"] = _combine_32bit(registers[0], registers[1])
        data["gen_month"] = _combine_32bit(registers[2], registers[3])
        data["gen_year"] = _combine_32bit(registers[4], registers[5])
        data["gen_total"] = _combine_32bit(registers[6], registers[7])
    elif batch == "consumed_energy" and len(registers) >= 8:
        data["use_today"] = _combine_32bit(registers[0], registers[1])
        data["use_month"] = _combine_32bit(registers[2], registers[3])
        data["use_year"] = _combine_32bit(registers[4], registers[5])
        data["use_total"] = _combine_32bit(registers[6], registers[7])


def _merge_load_state(
    data: dict, mode_registers: list[int] | None, coils: list[bool] | None
) -> None:
    """Merge load mode and load on/off state; absent if the read failed."""
    if mode_registers:
        mode = mode_registers[0]
        data["load_mode"] = LOAD_MODES.get(mode, f"Unknown({mode})")
    if coils:
        data["load_on"] = coils[0]


def _merge_settings_batch(data: dict, batch: str, registers: list[int] | None) -> None:
    """Merge one block of controller settings; absent if the read failed."""
    if not registers:
        return

    if batch == "battery_settings" and len(registers) >= 15:
        battery_type = registers[0]
        data["set_battery_type"] = BATTERY_TYPES.get(
            battery_type, f"Type {battery_type}"
        )
        data["set_battery_capacity"] = registers[1]
        data["set_temperature_compensation"] = registers[2] / 100.0
        for offset, key in _SETTING_VOLTAGES:
            data[key] = registers[offset] / 100.0
    elif batch == "charge_durations" and len(registers) >= 2:
        data["set_equalize_duration"] = registers[0]
        data["set_boost_duration"] = registers[1]
    elif batch == "clock" and len(registers) >= 3:
        second, minute = registers[0] & 0xFF, registers[0] >> 8
        hour, day = registers[1] & 0xFF, registers[1] >> 8
        month, year = registers[2] & 0xFF, registers[2] >> 8
        data["controller_clock"] = (
            f"{2000 + year:04d}-{month:02d}-{day:02d} "
            f"{hour:02d}:{minute:02d}:{second:02d}"
        )


def read_settings(ble: L2capBLE) -> dict:
    """Read the controller's battery settings, charge durations and clock."""
    data: dict = {}
    for index, (start, count, batch) in enumerate(SETTINGS_BATCHES):
        if index:
            time.sleep(READ_DELAY)
        _merge_settings_batch(data, batch, ble.read_holding_registers(start, count))
    return data


async def async_read_settings(ble: HomeAssistantBLE) -> dict:
    """Read the controller settings through an asynchronous transport."""
    data: dict = {}
    for index, (start, count, batch) in enumerate(SETTINGS_BATCHES):
        if index:
            await asyncio.sleep(READ_DELAY)
        _merge_settings_batch(
            data, batch, await ble.read_holding_registers(start, count)
        )
    return data


def read_all_data(ble: L2capBLE) -> dict:
    """Read all registers and return a flat dict of sensor values.

    This function is blocking (uses time.sleep between register reads)
    and must be called from an executor thread when used in async contexts.
    """
    data: dict = {}
    for index, (start, count, batch) in enumerate(REGISTER_BATCHES):
        if index:
            time.sleep(READ_DELAY)
        _merge_register_batch(data, batch, ble.read_input_registers(start, count))

    time.sleep(READ_DELAY)
    mode = ble.read_holding_registers(LOAD_MODE_REGISTER, 1)
    time.sleep(READ_DELAY)
    _merge_load_state(data, mode, ble.read_coils(LOAD_CONTROL_COIL, 1))
    return data


async def async_read_all_data(ble: HomeAssistantBLE) -> dict:
    """Read all registers through an asynchronous Home Assistant BLE transport."""
    data: dict = {}
    for index, (start, count, batch) in enumerate(REGISTER_BATCHES):
        if index:
            await asyncio.sleep(READ_DELAY)
        registers = await ble.read_input_registers(start, count)
        _merge_register_batch(data, batch, registers)

    await asyncio.sleep(READ_DELAY)
    mode = await ble.read_holding_registers(LOAD_MODE_REGISTER, 1)
    await asyncio.sleep(READ_DELAY)
    _merge_load_state(data, mode, await ble.read_coils(LOAD_CONTROL_COIL, 1))
    return data
