"""Common fixtures for SolarEdge Modbus Multi tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL

from custom_components.solaredge_modbus_multi.const import (
    ConfName,
)


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
        ConfName.SLEEP_AFTER_WRITE: 3,
        ConfName.BATTERY_RATING_ADJUST: 0,
        ConfName.BATTERY_ENERGY_RESET_CYCLES: 0,
    }


@pytest.fixture
def mock_modbus_client() -> Generator[MagicMock, None, None]:
    """Return a mock AsyncModbusTcpClient."""
    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient"
    ) as mock_client:
        client_instance = MagicMock()
        client_instance.connect = AsyncMock()
        client_instance.close = MagicMock()
        client_instance.connected = True
        client_instance.read_holding_registers = AsyncMock()
        client_instance.write_registers = AsyncMock()
        mock_client.return_value = client_instance
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


def create_modbus_response(registers: list[int]) -> MagicMock:
    """Create a mock Modbus response."""
    response = MagicMock()
    response.isError.return_value = False
    response.registers = registers
    return response


def create_exception_response(exception_code: int):
    """Create a mock ExceptionResponse with proper type."""

    try:
        from pymodbus.pdu.pdu import ExceptionResponse
    except ImportError:
        from pymodbus.pdu import ExceptionResponse

    # Create actual exception instance
    response = ExceptionResponse(0x01, exception_code)
    response.isError = MagicMock(return_value=True)
    return response


def create_io_exception_response():
    """Create a mock ModbusIOException response with proper type."""
    from pymodbus.exceptions import ModbusIOException

    response = ModbusIOException("Test IO Exception")
    response.isError = MagicMock(return_value=True)
    return response
