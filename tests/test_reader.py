import asyncio

import epever_ble
from epever_ble import __main__ as epever_cli
from epever_ble import async_read_all_data, read_all_data


class FakeBLE:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self.responses = {
            (0x3100, 8): [398, 0, 0, 0, 1303, 0, 0, 0],
            (0x3108, 4): [1298, 314, 4075, 0],
            (0x310C, 8): [1303, 842, 10779, 0, 2500, 2630, 2800, 0],
            (0x311A, 2): [68, 0],
            (0x3200, 3): [0, 2 << 2, 0],
            (0x331B, 2): [0x007B, 0x0000],
            (0x330C, 8): [1, 0, 2, 0, 3, 0, 4, 0],
            (0x3304, 8): [5, 0, 6, 0, 7, 0, 8, 0],
        }

        self.load_mode = [0]
        self.load_coil = [True]
        self.load_calls: list[tuple[str, int, int]] = []

    def read_input_registers(self, start: int, count: int, slave: int = 1) -> list[int]:
        self.calls.append((start, count, slave))
        return self.responses[(start, count)]

    def read_holding_registers(self, start: int, count: int, slave: int = 1):
        self.load_calls.append(("holding", start, count))
        return self.load_mode

    def read_coils(self, start: int, count: int = 1, slave: int = 1):
        self.load_calls.append(("coils", start, count))
        return self.load_coil


def test_read_all_data_maps_xtra3210n_g3_register_values(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)
    ble = FakeBLE()

    data = read_all_data(ble)

    assert data == {
        "pv_voltage": 3.98,
        "pv_current": 0.0,
        "pv_power": 0.0,
        "pv_output_voltage": 13.03,
        "batt_charge_current": 0.0,
        "batt_charge_power": 0.0,
        "batt_voltage": 12.98,
        "batt_output_current": 3.14,
        "batt_output_power": 40.75,
        "load_voltage": 13.03,
        "load_current": 8.42,
        "load_power": 107.79,
        "batt_temp": 25.0,
        "device_temp": 26.3,
        "mosfet_temp": 28.0,
        "batt_soc": 68,
        "charge_mode": "Boost",
        "batt_net_current": 1.23,
        "gen_today": 0.01,
        "gen_month": 0.02,
        "gen_year": 0.03,
        "gen_total": 0.04,
        "use_today": 0.05,
        "use_month": 0.06,
        "use_year": 0.07,
        "use_total": 0.08,
        "load_mode": "Manual",
        "load_on": True,
    }
    assert ble.load_calls == [("holding", 0x903D, 1), ("coils", 0x0002, 1)]
    assert ble.calls == [
        (0x3100, 8, 1),
        (0x3108, 4, 1),
        (0x310C, 8, 1),
        (0x311A, 2, 1),
        (0x3200, 3, 1),
        (0x331B, 2, 1),
        (0x330C, 8, 1),
        (0x3304, 8, 1),
    ]


def test_read_all_data_parses_negative_net_battery_current(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)
    ble = FakeBLE()
    ble.responses[(0x331B, 2)] = [0xFEC6, 0xFFFF]

    data = read_all_data(ble)

    assert data["batt_net_current"] == -3.14


def test_display_data_shows_distinct_batt_net_current(capsys) -> None:
    data = {
        "pv_voltage": 12.0,
        "batt_voltage": 12.5,
        "batt_charge_current": 4.2,
        "batt_net_current": -1.75,
    }

    epever_cli.display_data(data)
    out = capsys.readouterr().out

    assert "  Charge Current:" in out
    assert "  Net Current:" in out
    assert "-1.75" in out


def test_display_data_shows_partial_net_current(capsys) -> None:
    epever_cli.display_data({"batt_net_current": -3.14})

    out = capsys.readouterr().out

    assert "  --- Battery ---" in out
    assert "  Net Current:" in out
    assert "-3.14" in out


def test_read_all_data_keeps_missing_batches_absent(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)

    class NoDataBLE:
        def read_input_registers(self, start: int, count: int, slave: int = 1):
            return None

        def read_holding_registers(self, start: int, count: int, slave: int = 1):
            return None

        def read_coils(self, start: int, count: int = 1, slave: int = 1):
            return None

    assert read_all_data(NoDataBLE()) == {}


def test_async_read_all_data_matches_sync_reader(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.asyncio, "sleep", _no_async_sleep)
    sync_ble = FakeBLE()

    class AsyncFakeBLE:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []

        async def read_input_registers(
            self, start: int, count: int, slave: int = 1
        ) -> list[int]:
            self.calls.append((start, count, slave))
            return sync_ble.responses[(start, count)]

        async def read_holding_registers(self, start, count, slave=1):
            return sync_ble.load_mode

        async def read_coils(self, start, count=1, slave=1):
            return sync_ble.load_coil

    async_ble = AsyncFakeBLE()
    data = asyncio.run(async_read_all_data(async_ble))

    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)
    assert data == read_all_data(sync_ble)
    assert async_ble.calls == sync_ble.calls


async def _no_async_sleep(_delay: float) -> None:
    return None


def test_read_all_data_reports_unknown_load_mode(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)
    ble = FakeBLE()
    ble.load_mode = [2]
    ble.load_coil = [False]

    data = read_all_data(ble)

    assert data["load_mode"] == "Light On + Timer"
    assert data["load_on"] is False


def test_read_all_data_omits_load_state_when_bridge_rejects_it(monkeypatch) -> None:
    monkeypatch.setattr(epever_ble._reader_mod.time, "sleep", lambda _: None)
    ble = FakeBLE()
    ble.load_mode = None
    ble.load_coil = None

    data = read_all_data(ble)

    assert "load_mode" not in data
    assert "load_on" not in data
    assert data["batt_voltage"] == 12.98


def test_display_data_shows_load_mode_and_state(capsys) -> None:
    epever_cli.display_data({"load_voltage": 0.0, "load_mode": "Manual", "load_on": False})

    out = capsys.readouterr().out

    assert "Manual" in out
    assert "Output:" in out
    assert "OFF" in out


class SwitchBLE:
    def __init__(self, mode, confirm=True) -> None:
        self.mode = mode
        self.confirm = confirm
        self.coil = False
        self.writes: list[tuple[int, bool]] = []

    def read_holding_registers(self, start, count, slave=1):
        return self.mode

    def write_coil(self, address, on, slave=1):
        self.writes.append((address, on))
        if self.confirm:
            self.coil = on
        return self.confirm

    def read_coils(self, start, count=1, slave=1):
        return [self.coil]


def test_switch_load_writes_manual_coil_and_confirms(monkeypatch, capsys) -> None:
    monkeypatch.setattr(epever_cli.time, "sleep", lambda _: None)
    ble = SwitchBLE([0])

    assert epever_cli.switch_load(ble, True)
    assert ble.writes == [(0x0002, True)]
    assert "now ON" in capsys.readouterr().out


def test_switch_load_refuses_outside_manual_mode(capsys) -> None:
    ble = SwitchBLE([1])

    assert not epever_cli.switch_load(ble, True)
    assert ble.writes == []
    assert "Light On/Off" in capsys.readouterr().out


def test_switch_load_reports_unconfirmed_command(monkeypatch) -> None:
    monkeypatch.setattr(epever_cli.time, "sleep", lambda _: None)
    ble = SwitchBLE([0], confirm=False)

    assert not epever_cli.switch_load(ble, False)
