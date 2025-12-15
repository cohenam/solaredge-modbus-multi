"""Common fixtures for SolarEdge Modbus Multi tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant

from custom_components.solaredge_modbus_multi.const import (
    DOMAIN,
    ConfDefaultFlag,
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
    registers.append(101)  # C_SunSpec_DID (single phase inverter)
    registers.append(65)  # C_SunSpec_Length

    # C_Manufacturer - "SolarEdge" padded to 32 chars (16 registers)
    manufacturer = "SolarEdge".ljust(32, "\x00")
    registers.extend([ord(manufacturer[i]) << 8 | ord(manufacturer[i+1])
                     for i in range(0, 32, 2)])

    # C_Model - "SE10K" padded to 32 chars (16 registers)
    model = "SE10K".ljust(32, "\x00")
    registers.extend([ord(model[i]) << 8 | ord(model[i+1])
                     for i in range(0, 32, 2)])

    # C_Option - padded to 16 chars (8 registers)
    option = "".ljust(16, "\x00")
    registers.extend([ord(option[i]) << 8 | ord(option[i+1])
                     for i in range(0, 16, 2)])

    # C_Version - "1.0.0" padded to 16 chars (8 registers)
    version = "1.0.0".ljust(16, "\x00")
    registers.extend([ord(version[i]) << 8 | ord(version[i+1])
                     for i in range(0, 16, 2)])

    # C_SerialNumber - "123456789" padded to 32 chars (16 registers)
    serial = "123456789".ljust(32, "\x00")
    registers.extend([ord(serial[i]) << 8 | ord(serial[i+1])
                     for i in range(0, 32, 2)])

    # C_Device_address
    registers.append(1)

    return registers


@pytest.fixture
def mock_inverter_model_registers() -> list[int]:
    """Return mock inverter model register data (40069-40108)."""
    return [
        # AC Current (A) x 10
        100, 100, 100, 100,  # I_AC_Current, I_AC_CurrentA/B/C
        0, 0,  # padding
        -2,  # I_AC_Current_SF
        # AC Voltage (V) x 10
        2400, 2400, 2400,  # I_AC_VoltageAB/BC/CA
        2400, 2400, 2400,  # I_AC_VoltageAN/BN/CN
        -1,  # I_AC_Voltage_SF
        # AC Power (W)
        5000,  # I_AC_Power
        -1,  # I_AC_Power_SF (x0.1)
        # AC Frequency (Hz) x 100
        5000,  # I_AC_Frequency
        -2,  # I_AC_Frequency_SF
        # Apparent Power (VA)
        5000, -1,  # I_AC_VA, I_AC_VA_SF
        # Reactive Power (VAr)
        0, -1,  # I_AC_VAR, I_AC_VAR_SF
        # Power Factor (%)
        100, -2,  # I_AC_PF, I_AC_PF_SF
        # Energy (Wh)
        0, 10000,  # I_AC_Energy_WH (high, low)
        0,  # I_AC_Energy_WH_SF
        # DC Current (A)
        200, -1,  # I_DC_Current, I_DC_Current_SF
        # DC Voltage (V)
        4000, -1,  # I_DC_Voltage, I_DC_Voltage_SF
        # DC Power (W)
        5100, -1,  # I_DC_Power, I_DC_Power_SF
        # Temperature (C)
        450, -1,  # I_Temp_Sink, I_Temp_SF
        # Status
        4, 0,  # I_Status, I_Status_Vendor
    ]


def create_modbus_response(registers: list[int]) -> MagicMock:
    """Create a mock Modbus response."""
    response = MagicMock()
    response.isError.return_value = False
    response.registers = registers
    return response
