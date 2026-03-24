"""EPEver BLE — communicate with EPEver charge controllers over Bluetooth.

Thin wrapper that re-exports from custom_components/epever_ble/ so the
BLE protocol code lives in one place (the HA custom component is
self-contained).
"""

import importlib.util
import os
import sys

_component_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components",
    "epever_ble",
)


def _load_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ble_mod = _load_module("epever_ble._ble", os.path.join(_component_dir, "ble.py"))
_reader_mod = _load_module("epever_ble._reader", os.path.join(_component_dir, "reader.py"))

L2capBLE = _ble_mod.L2capBLE
build_modbus_read = _ble_mod.build_modbus_read
modbus_crc16 = _ble_mod.modbus_crc16
verify_modbus_crc = _ble_mod.verify_modbus_crc
read_all_data = _reader_mod.read_all_data
CHARGING_MODES = _reader_mod.CHARGING_MODES

__all__ = [
    "L2capBLE",
    "build_modbus_read",
    "modbus_crc16",
    "verify_modbus_crc",
    "read_all_data",
    "CHARGING_MODES",
]
