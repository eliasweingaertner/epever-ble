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
