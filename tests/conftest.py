"""Common fixtures for SolarEdge Modbus Multi tests."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from modbus_connection.exceptions import ModbusExceptionError, ModbusTimeoutError
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import ConfName
from tests.fake_modbus_server import FakeModbusServer


@pytest.fixture
def mock_config_entry_data() -> dict[str, Any]:
    """Return mock config entry data."""
    return {
        CONF_HOST: "192.168.1.100",
        CONF_PORT: 1502,
        CONF_NAME: "Test SolarEdge",
        ConfName.DEVICE_LIST: [1],
    }


@pytest.fixture
def mock_config_entry_options() -> dict[str, Any]:
    """Return mock config entry options."""
    return {
        CONF_SCAN_INTERVAL: 300,
        ConfName.DETECT_METERS: True,
        ConfName.DETECT_BATTERIES: True,
        ConfName.DETECT_EXTRAS: False,
        ConfName.KEEP_MODBUS_OPEN: False,
        ConfName.ADV_STORAGE_CONTROL: False,
        ConfName.ADV_SITE_LIMIT_CONTROL: False,
        ConfName.ALLOW_BATTERY_ENERGY_RESET: False,
        # Writes are off by default in production; the suite opts in so it can
        # exercise encoding and failure handling. Gate-off behaviour has its
        # own tests in test_write_gate.py.
        ConfName.ALLOW_HARDWARE_WRITES: True,
        ConfName.SLEEP_AFTER_WRITE: 3,
        ConfName.BATTERY_RATING_ADJUST: 0,
        ConfName.BATTERY_ENERGY_RESET_CYCLES: 0,
    }


@pytest.fixture
def mock_config_entry() -> MagicMock:
    """Create a mock config entry for entity construction."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {"name": "Test SolarEdge"}
    return entry


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator for entity construction."""
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = {}
    return coordinator


def assert_golden(path: Path, rendered: str, *, drift: str) -> None:
    """Compare output against a committed golden, failing closed if absent.

    A golden that regenerates itself when missing passes without evidence,
    which makes it useless exactly when it matters most. Regeneration is
    therefore explicit and noisy: set the fixture's regenerate variable and
    the file is rewritten, then the test fails anyway, so the new values have
    to be looked at before they can be committed.
    """
    variable = f"SOLAREDGE_REGENERATE_{path.stem.upper()}"

    if os.environ.get(variable):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        pytest.fail(
            f"{variable} was set, so {path.name} was rewritten. Review the "
            "diff, commit it, and re-run without the variable."
        )

    assert path.exists(), (
        f"{path} is missing. It is committed evidence, not a cache — restore "
        f"it from git. To rebuild it deliberately, run with {variable}=1."
    )

    # Rendered text, not parsed JSON: Python equality makes 1 == 1.0 == True,
    # so a parsed comparison would accept a float or a bool where the wire
    # needs an int. The parsed diff is only used to describe the failure.
    committed = path.read_text()
    if rendered != committed:
        raise AssertionError(
            f"{drift} If intentional, re-run with {variable}=1.\n"
            f"parsed diff: {json.loads(rendered)!r}\n"
            f"       != : {json.loads(committed)!r}"
        )


async def _call_unit_method(connection: MagicMock, name: str, *args, **kwargs):
    """Forward a per-unit call to the connection-level recorder.

    The recorder is looked up per call, so a test may swap it out after the
    unit handle exists. modbus-connection reports every failure by raising,
    but a mock can only hand one back — through `return_value` or a
    `side_effect` function's return. Anything exception-shaped coming back
    is therefore re-raised here, which is what makes the response helpers
    below work either way.
    """
    result = await getattr(connection, name)(*args, **kwargs)
    if isinstance(result, BaseException):
        raise result
    return result


def connection_double() -> MagicMock:
    """Build a stand-in for modbus_connection's ModbusConnection.

    Register traffic is configured on the connection itself
    (`client.read_holding_registers.side_effect = ...`), while the transport
    reaches it through `for_unit(unit)`. Every unit handle forwards to the
    same recorders, so one side_effect still sees the whole session and
    `assert_called_once()` still counts every unit's calls.
    """
    connection = MagicMock()
    # A fresh ModbusConnection has no client yet, and its close() is
    # permanent; the transport's connect/reconnect accounting keys off this.
    connection.connected = False

    def _connected(value: bool) -> None:
        connection.connected = value

    connection.connect = AsyncMock(side_effect=lambda: _connected(True))
    connection.close = AsyncMock(side_effect=lambda: _connected(False))
    connection.on_connection_lost = MagicMock()
    connection.read_holding_registers = AsyncMock()
    connection.write_registers = AsyncMock()
    connection.set_message_spacing = MagicMock()

    handles: dict[int, MagicMock] = {}

    def for_unit(unit_id: int) -> MagicMock:
        if unit_id not in handles:
            handle = MagicMock()
            handle.unit_id = unit_id
            handle.read_holding_registers = partial(
                _call_unit_method, connection, "read_holding_registers"
            )
            handle.write_registers = partial(
                _call_unit_method, connection, "write_registers"
            )
            handle.set_message_spacing = connection.set_message_spacing
            handles[unit_id] = handle
        return handles[unit_id]

    connection.for_unit.side_effect = for_unit
    return connection


def install_connection_double(transport) -> MagicMock:
    """Point a transport at doubles, including the ones it rebuilds.

    Every close is a recycle now, so a test that keeps polling across a
    failure needs the next generation to be a double too — otherwise the
    transport builds a real ModbusConnection and opens a socket.
    """
    transport._connection_factory = lambda **kwargs: connection_double()
    transport._connection = connection_double()
    transport._connection.connected = True
    return transport._connection


@pytest.fixture
def mock_modbus_client() -> Generator[MagicMock, None, None]:
    """Patch the transport's ModbusConnection with a connection double."""
    with patch(
        "custom_components.solaredge_modbus_multi.modbus_transport.ModbusConnection"
    ) as mock_client:
        mock_client.return_value = connection_double()
        yield mock_client


@pytest.fixture
def mock_connection(mock_modbus_client) -> MagicMock:
    """The connection double the patched transport builds."""
    return mock_modbus_client.return_value


@pytest.fixture
def _allow_sockets(socket_enabled):
    """Tests that request this intentionally use real localhost sockets."""
    yield


@pytest.fixture
async def make_server():
    """Start FakeModbusServer instances, stopping them all at teardown."""
    servers: list[FakeModbusServer] = []

    async def _make(**kwargs) -> FakeModbusServer:
        server = FakeModbusServer(**kwargs)
        await server.start()
        servers.append(server)
        return server

    yield _make

    # stop() is idempotent, so servers a test already stopped inline are fine.
    for server in servers:
        await server.stop()


@pytest.fixture
def mock_inverter_registers() -> list[int]:
    """Return mock inverter common register data (40000-40068)."""
    # SunSpec ID (0x53756E53 = "SunS")
    registers = [0x5375, 0x6E53]  # C_SunSpec_ID
    registers.append(1)  # C_SunSpec_DID (common block = 1)
    registers.append(65)  # C_SunSpec_Length

    # C_Manufacturer - "SolarEdge" padded to 32 chars (16 registers)
    manufacturer = "SolarEdge".ljust(32, "\x00")
    registers.extend(
        [ord(manufacturer[i]) << 8 | ord(manufacturer[i + 1]) for i in range(0, 32, 2)]
    )

    # C_Model - "SE10K" padded to 32 chars (16 registers)
    model = "SE10K".ljust(32, "\x00")
    registers.extend([ord(model[i]) << 8 | ord(model[i + 1]) for i in range(0, 32, 2)])

    # C_Option - padded to 16 chars (8 registers)
    option = "".ljust(16, "\x00")
    registers.extend(
        [ord(option[i]) << 8 | ord(option[i + 1]) for i in range(0, 16, 2)]
    )

    # C_Version - "1.0.0" padded to 16 chars (8 registers)
    version = "1.0.0".ljust(16, "\x00")
    registers.extend(
        [ord(version[i]) << 8 | ord(version[i + 1]) for i in range(0, 16, 2)]
    )

    # C_SerialNumber - "123456789" padded to 32 chars (16 registers)
    serial = "123456789".ljust(32, "\x00")
    registers.extend(
        [ord(serial[i]) << 8 | ord(serial[i + 1]) for i in range(0, 32, 2)]
    )

    # C_Device_address
    registers.append(1)

    return registers


@pytest.fixture
def mock_inverter_model_registers() -> list[int]:
    """Return mock inverter model register data (40069-40108).

    This represents 40 registers starting at address 40069.
    The structure follows SunSpec model 101/102/103.
    """

    # Convert signed int16 to unsigned for Modbus
    def s16(val):
        return val if val >= 0 else val + 65536

    return [
        101,  # C_SunSpec_DID (101 = single phase inverter)
        50,  # C_SunSpec_Length
        100,  # AC_Current (reg 2)
        100,  # AC_Current_A (reg 3)
        100,  # AC_Current_B (reg 4)
        100,  # AC_Current_C (reg 5)
        s16(-2),  # AC_Current_SF (reg 6)
        2400,  # AC_Voltage_AB (reg 7)
        2400,  # AC_Voltage_BC (reg 8)
        2400,  # AC_Voltage_CA (reg 9)
        2400,  # AC_Voltage_AN (reg 10)
        2400,  # AC_Voltage_BN (reg 11)
        2400,  # AC_Voltage_CN (reg 12)
        s16(-1),  # AC_Voltage_SF (reg 13)
        5000,  # AC_Power (reg 14)
        s16(-1),  # AC_Power_SF (reg 15)
        5000,  # AC_Frequency (reg 16)
        s16(-2),  # AC_Frequency_SF (reg 17)
        5000,  # AC_VA (reg 18)
        s16(-1),  # AC_VA_SF (reg 19)
        0,  # AC_VAR (reg 20)
        s16(-1),  # AC_VAR_SF (reg 21)
        100,  # AC_PF (reg 22)
        s16(-2),  # AC_PF_SF (reg 23)
        0,  # AC_Energy_WH high (reg 24)
        10000,  # AC_Energy_WH low (reg 25)
        0,  # AC_Energy_WH_SF (reg 26)
        200,  # I_DC_Current (reg 27)
        s16(-1),  # I_DC_Current_SF (reg 28)
        4000,  # I_DC_Voltage (reg 29)
        s16(-1),  # I_DC_Voltage_SF (reg 30)
        5100,  # I_DC_Power (reg 31)
        s16(-1),  # I_DC_Power_SF (reg 32)
        450,  # I_Temp_Cab (reg 33)
        450,  # I_Temp_Sink (reg 34)
        0,  # I_Temp_Trns (reg 35)
        0,  # I_Temp_Other (reg 36)
        s16(-1),  # I_Temp_SF (reg 37)
        4,  # I_Status (reg 38) - 4 = MPPT
        0,  # I_Status_Vendor (reg 39)
    ]


def create_modbus_response(registers: list[int]) -> list[int]:
    """Create a successful read result: modbus-connection yields registers."""
    return list(registers)


def create_exception_response(exception_code: int) -> ModbusExceptionError:
    """Create the failure a device's exception PDU raises."""
    return ModbusExceptionError(exception_code)


def create_io_exception_response() -> ModbusTimeoutError:
    """Create the failure an unanswered request raises."""
    return ModbusTimeoutError("Test IO Exception")


def registers_from_values(*typed_values, word_order: str = "little") -> list[int]:
    """Build a register block from (value, DATATYPE) pairs.

    Encodes each value with the same word order the hub uses to decode,
    so tests exercise the real offset/datatype mapping instead of zeros.
    Little word order is the default (SolarEdge proprietary blocks);
    pass word_order="big" for standard SunSpec 32-bit values.
    """
    from pymodbus.client.mixin import ModbusClientMixin

    registers: list[int] = []
    for value, data_type in typed_values:
        registers.extend(
            ModbusClientMixin.convert_to_registers(
                value, data_type=data_type, word_order=word_order
            )
        )
    return registers


def string_registers(text: str, register_count: int) -> list[int]:
    """Encode a SunSpec string field: two big-endian chars per register."""
    padded = text.ljust(register_count * 2, "\x00")
    return [
        ord(padded[i]) << 8 | ord(padded[i + 1])
        for i in range(0, register_count * 2, 2)
    ]


# --- full register spaces for driving real device reads -----------------------

DT = ModbusClientMixin.DATATYPE


def s16(val: int) -> int:
    return val & 0xFFFF


def _place(space: dict[int, int], address: int, registers: list[int]) -> None:
    for offset, value in enumerate(registers):
        space[address + offset] = value


def _inverter_common(model: str, version: str, serial: str) -> list[int]:
    regs = registers_from_values((0x53756E53, DT.UINT32), word_order="big")
    regs += [1, 65]  # C_SunSpec_DID, C_SunSpec_Length
    regs += string_registers("SolarEdge", 16)
    regs += string_registers(model, 16)
    regs += string_registers("E0", 8)
    regs += string_registers(version, 8)
    regs += string_registers(serial, 16)
    regs += [1]  # C_Device_address
    return regs


def _inverter_model(did: int) -> list[int]:
    """Model 101/103 block (40 registers at 40069), healthy values."""
    return [
        did,  # C_SunSpec_DID
        50,  # C_SunSpec_Length
        120,  # AC_Current
        40,  # AC_Current_A
        40,  # AC_Current_B
        40,  # AC_Current_C
        s16(-1),  # AC_Current_SF
        4000,  # AC_Voltage_AB
        4001,  # AC_Voltage_BC
        4002,  # AC_Voltage_CA
        2310,  # AC_Voltage_AN
        2311,  # AC_Voltage_BN
        2312,  # AC_Voltage_CN
        s16(-1),  # AC_Voltage_SF
        25000,  # AC_Power
        0,  # AC_Power_SF
        5001,  # AC_Frequency
        s16(-2),  # AC_Frequency_SF
        25500,  # AC_VA
        0,  # AC_VA_SF
        s16(-1200),  # AC_var
        0,  # AC_var_SF
        s16(-9800),  # AC_PF
        s16(-2),  # AC_PF_SF
        *registers_from_values((123_456_789, DT.UINT32), word_order="big"),
        0,  # AC_Energy_WH_SF
        330,  # I_DC_Current
        s16(-1),  # I_DC_Current_SF
        7500,  # I_DC_Voltage
        s16(-1),  # I_DC_Voltage_SF
        25400,  # I_DC_Power
        0,  # I_DC_Power_SF
        451,  # I_Temp_Cab
        452,  # I_Temp_Sink
        0,  # I_Temp_Trns
        0,  # I_Temp_Other
        s16(-1),  # I_Temp_SF
        4,  # I_Status (MPPT)
        0,  # I_Status_Vendor
    ]


def _mmppt_block() -> list[int]:
    """Model 160 data (48 registers at 40123, 2 Synergy units)."""
    regs = [s16(-2), s16(-1), 0, 0]  # DCA/DCV/DCW/DCWH SFs
    regs += registers_from_values((0, DT.UINT32), word_order="big")  # Events
    regs += [2, 0]  # N units, TmsPer
    for unit in range(2):
        regs += [unit + 1]  # ID
        regs += string_registers(f"MPPT-{unit}", 8)  # IDStr
        regs += [55 + unit, 7500 + unit, 12500 + unit]  # DCA, DCV, DCW
        regs += registers_from_values((6_100_000 + unit, DT.UINT32), word_order="big")
        regs += registers_from_values((0, DT.UINT32), word_order="big")  # Tms
        regs += [380 + unit, 4]  # Tmp, DCSt
        regs += registers_from_values((0, DT.UINT32), word_order="big")  # DCEvt
    assert len(regs) == 48
    return regs


def _meter_common(serial: str) -> list[int]:
    regs = [1, 65]  # C_SunSpec_DID, C_SunSpec_Length
    regs += string_registers("WattNode", 16)
    regs += string_registers("WNC-3Y-400-MB", 16)
    regs += string_registers("Export+Import", 8)
    regs += string_registers("25", 8)
    regs += string_registers(serial, 16)
    regs += [2]  # C_Device_address
    return regs


def _meter_data(did: int) -> list[int]:
    """Meter model block (107 registers), mirroring the hub's exact slices.

    The uint32 stream is scattered to positions [38:54]+[55:70]+[71:104]+
    [105:107] — the same index expressions the decode concatenates. Note
    register 70 is never read and register 71 feeds BOTH the M_VAh_SF
    int16 and the uint32 stream; the golden pins that behavior as-is.
    """
    regs = [0] * 107
    regs[0], regs[1] = did, 105
    for position, value in zip(range(2, 38), range(-18, 18), strict=True):
        regs[position] = s16(value)
    regs[54] = 0  # AC_Energy_WH_SF
    regs[104] = s16(-2)  # M_varh_SF

    stream: list[int] = []
    for index in range(33):
        stream += registers_from_values(
            (1_000_000 + 1_000 * index, DT.UINT32), word_order="big"
        )
    positions = (
        list(range(38, 54))
        + list(range(55, 70))
        + list(range(71, 104))
        + list(range(105, 107))
    )
    for position, value in zip(positions, stream, strict=True):
        regs[position] = value
    return regs


def _battery_common() -> list[int]:
    regs = string_registers("SolarEdge", 16)
    regs += string_registers("Home Battery 48V", 16)
    regs += string_registers("DCDC 2.0.15", 16)
    regs += string_registers("BSN-4242", 16)
    regs += [3, 0]  # B_Device_Address, pad
    regs += registers_from_values((9700.0, DT.FLOAT32))
    assert len(regs) == 68
    return regs


def _battery_data() -> list[int]:
    regs = [0] * 86
    _scatter = registers_from_values(
        (5000.0, DT.FLOAT32),  # B_MaxChargePower
        (5000.0, DT.FLOAT32),  # B_MaxDischargePower
        (7500.0, DT.FLOAT32),  # B_MaxChargePeakPower
        (7500.0, DT.FLOAT32),  # B_MaxDischargePeakPower
    )
    regs[0:8] = _scatter
    regs[40:50] = registers_from_values(
        (28.5, DT.FLOAT32),  # B_Temp_Average
        (30.0, DT.FLOAT32),  # B_Temp_Max
        (400.0, DT.FLOAT32),  # B_DC_Voltage
        (12.5, DT.FLOAT32),  # B_DC_Current
        (5000.0, DT.FLOAT32),  # B_DC_Power
    )
    regs[50:58] = registers_from_values(
        (3_500_000, DT.UINT64),  # B_Export_Energy_WH
        (4_200_000, DT.UINT64),  # B_Import_Energy_WH
    )
    regs[58:66] = registers_from_values(
        (9700.0, DT.FLOAT32),  # B_Energy_Max
        (8000.0, DT.FLOAT32),  # B_Energy_Available
        (99.0, DT.FLOAT32),  # B_SOH
        (82.5, DT.FLOAT32),  # B_SOE
    )
    regs[66:70] = registers_from_values(
        (3, DT.UINT32),  # B_Status
        (0, DT.UINT32),  # B_Status_Vendor
    )
    regs[70:86] = [0] * 16  # event logs
    return regs


def _control_blocks(space: dict[int, int]) -> None:
    """GPC, APC, site limit and storage blocks (all little word order)."""
    _place(
        space,
        61440,
        registers_from_values((0, DT.UINT16), (100, DT.UINT16), (1.0, DT.FLOAT32)),
    )

    apc1 = [0] * 86
    apc1[0:2] = registers_from_values((0, DT.INT16), (0, DT.INT16))
    apc1[2:6] = registers_from_values((1, DT.INT32), (4, DT.INT32))
    apc1[6:8] = registers_from_values((60, DT.UINT32))
    apc1[8:10] = registers_from_values((100, DT.INT32))
    block1_floats = [(float(n), DT.FLOAT32) for n in range(1, 29)]
    apc1[10:66] = registers_from_values(*block1_floats)
    apc1[66:70] = registers_from_values((1, DT.INT32), (0, DT.INT32))
    apc1[70:86] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(29, 37)]
    )
    _place(space, 61696, apc1)

    apc2 = [0] * 84
    apc2[0:32] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(101, 117)]
    )
    apc2[32:36] = registers_from_values((300, DT.UINT32), (600, DT.UINT32))
    apc2[36:52] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(117, 125)]
    )
    apc2[52:56] = registers_from_values((900, DT.UINT32), (0, DT.UINT32))
    apc2[56:84] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(125, 139)]
    )
    _place(space, 61782, apc2)

    _place(
        space,
        57344,
        registers_from_values((1, DT.UINT16), (0, DT.UINT16), (15000.0, DT.FLOAT32)),
    )
    _place(space, 57362, registers_from_values((18000.0, DT.FLOAT32)))

    storage = registers_from_values((1, DT.UINT16), (1, DT.UINT16))
    storage += registers_from_values((6600.0, DT.FLOAT32), (25.0, DT.FLOAT32))
    storage += registers_from_values((7, DT.UINT16))
    storage += registers_from_values((3600, DT.UINT32))
    storage += registers_from_values((3, DT.UINT16))
    storage += registers_from_values((5000.0, DT.FLOAT32), (5000.0, DT.FLOAT32))
    assert len(storage) == 14
    _place(space, 57348, storage)


def build_synergy_full_space() -> dict[int, int]:
    space: dict[int, int] = {}
    _place(space, 40000, _inverter_common("SE100K", "0004.0021.0000", "SN-A1234567"))
    _place(space, 40069, _inverter_model(103))
    _place(space, 40113, registers_from_values((1, DT.UINT32)))  # I_Grid_Status
    _place(
        space, 40119, registers_from_values((0x00030000, DT.UINT32), word_order="big")
    )
    _place(space, 40121, [160, 48])  # MMPPT header
    _place(space, 40123, _mmppt_block())
    _place(space, 40171, _meter_common("MSN-A777"))  # Synergy: 40121 + 50
    _place(space, 40238, _meter_data(203))
    _place(space, 57600, _battery_common())
    _place(space, 57668, _battery_data())
    _control_blocks(space)
    return space


def make_side_effect(space, overrides, calls):
    def side_effect(*args, **kwargs):
        address = kwargs.get("address", args[0] if args else 0)
        count = kwargs.get("count", args[1] if len(args) > 1 else 1)
        calls.append((address, count))
        if address in overrides:
            return overrides[address]
        return create_modbus_response(
            [space.get(address + offset, 0) for offset in range(count)]
        )

    return side_effect
