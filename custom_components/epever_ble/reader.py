"""Register reading and data parsing for EPEver charge controllers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ble import L2capBLE

CHARGING_MODES = {0: "Not Charging", 1: "Float", 2: "Boost", 3: "Equalization"}


def _combine_32bit(low: int, high: int) -> float:
    return (high * 65536 + low) / 100.0


def _signed_temp(val: int) -> float:
    if val > 32767:
        val -= 65536
    return val / 100.0


def read_all_data(ble: L2capBLE) -> dict:
    """Read all registers and return a flat dict of sensor values.

    This function is blocking (uses time.sleep between register reads)
    and must be called from an executor thread when used in async contexts.
    """
    data: dict = {}
    delay = 0.3

    # PV + Battery (0x3100-0x3107)
    regs = ble.read_input_registers(0x3100, 8)
    if regs:
        n = len(regs)
        if n > 0: data["pv_voltage"] = regs[0] / 100.0
        if n > 1: data["pv_current"] = regs[1] / 100.0
        if n > 3: data["pv_power"] = _combine_32bit(regs[2], regs[3])
        if n > 4: data["batt_voltage"] = regs[4] / 100.0
        if n > 5: data["batt_charge_current"] = regs[5] / 100.0
        if n > 7: data["batt_charge_power"] = _combine_32bit(regs[6], regs[7])

    # Load + Temp (0x310C-0x3113)
    time.sleep(delay)
    regs2 = ble.read_input_registers(0x310C, 8)
    if regs2:
        n = len(regs2)
        if n > 0: data["load_voltage"] = regs2[0] / 100.0
        if n > 1: data["load_current"] = regs2[1] / 100.0
        if n > 3: data["load_power"] = _combine_32bit(regs2[2], regs2[3])
        if n > 4: data["batt_temp"] = _signed_temp(regs2[4])
        if n > 5: data["device_temp"] = _signed_temp(regs2[5])

    # Battery SOC
    time.sleep(delay)
    soc_regs = ble.read_input_registers(0x311A, 2)
    if soc_regs:
        data["batt_soc"] = soc_regs[0]

    # Charging status
    time.sleep(delay)
    status_regs = ble.read_input_registers(0x3200, 3)
    if status_regs and len(status_regs) >= 2:
        charge_mode = (status_regs[1] >> 2) & 0x03
        data["charge_mode"] = CHARGING_MODES.get(charge_mode, f"Unknown({charge_mode})")

    # Generated energy (0x330C-0x3313)
    time.sleep(delay)
    gen_regs = ble.read_input_registers(0x330C, 8)
    if gen_regs and len(gen_regs) >= 8:
        data["gen_today"] = _combine_32bit(gen_regs[0], gen_regs[1])
        data["gen_month"] = _combine_32bit(gen_regs[2], gen_regs[3])
        data["gen_year"] = _combine_32bit(gen_regs[4], gen_regs[5])
        data["gen_total"] = _combine_32bit(gen_regs[6], gen_regs[7])

    # Consumed energy (0x3304-0x330B)
    time.sleep(delay)
    use_regs = ble.read_input_registers(0x3304, 8)
    if use_regs and len(use_regs) >= 8:
        data["use_today"] = _combine_32bit(use_regs[0], use_regs[1])
        data["use_month"] = _combine_32bit(use_regs[2], use_regs[3])
        data["use_year"] = _combine_32bit(use_regs[4], use_regs[5])
        data["use_total"] = _combine_32bit(use_regs[6], use_regs[7])

    return data
