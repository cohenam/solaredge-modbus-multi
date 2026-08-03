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

from custom_components.solaredge_modbus_multi.const import ConfName


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
