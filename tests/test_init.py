"""Tests for the SolarEdge Modbus Multi integration setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.solaredge_modbus_multi import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.solaredge_modbus_multi.const import DOMAIN
from custom_components.solaredge_modbus_multi.hub import HubInitFailed


@pytest.fixture
def mock_config_entry(hass, mock_config_entry_data, mock_config_entry_options):
    """Create a mock config entry."""
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        source="user",
        unique_id="192.168.1.100:1502",
        options=mock_config_entry_options,
    )
    entry.add_to_hass(hass)
    return entry


async def test_async_setup(hass: HomeAssistant) -> None:
    """Test async_setup initializes domain data."""
    result = await async_setup(hass, {})
    assert result is True
    assert DOMAIN in hass.data
    assert "yaml" in hass.data[DOMAIN]


async def test_async_setup_with_yaml_config(hass: HomeAssistant) -> None:
    """Test async_setup with YAML configuration."""
    yaml_config = {
        DOMAIN: {
            "retry": {
                "limit": 5,
                "time": 200,
                "ratio": 3,
            },
            "modbus": {
                "timeout": 5,
                "retries": 2,
            },
        }
    }
    result = await async_setup(hass, yaml_config)
    assert result is True
    assert hass.data[DOMAIN]["yaml"] == yaml_config[DOMAIN]


async def test_async_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry,
    mock_modbus_client,
    mock_inverter_registers,
    mock_inverter_model_registers,
) -> None:
    """Test successful setup of config entry."""
    from tests.conftest import create_modbus_response

    # Initialize domain data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    # Setup mock responses
    mock_client = mock_modbus_client.return_value

    def mock_read(address, count, **kwargs):
        if address == 40000:
            return create_modbus_response(mock_inverter_registers)
        elif address == 40069:
            return create_modbus_response(mock_inverter_model_registers)
        elif address == 40121:
            # MMPPT common - return not implemented
            return create_modbus_response([0xFFFF] * count)
        else:
            return create_modbus_response([0] * count)

    mock_client.read_holding_registers = AsyncMock(side_effect=mock_read)

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_async_setup_entry_connection_failed(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test setup fails with ConfigEntryNotReady on connection error."""
    # Initialize domain data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient"
    ) as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.connect = AsyncMock(side_effect=ConnectionError("Failed"))
        mock_instance.connected = False

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, mock_config_entry)


async def test_async_unload_entry(
    hass: HomeAssistant,
    mock_config_entry,
    mock_modbus_client,
    mock_inverter_registers,
    mock_inverter_model_registers,
) -> None:
    """Test unloading a config entry."""
    from tests.conftest import create_modbus_response

    # Initialize domain data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    # Setup mock responses
    mock_client = mock_modbus_client.return_value

    def mock_read(address, count, **kwargs):
        if address == 40000:
            return create_modbus_response(mock_inverter_registers)
        elif address == 40069:
            return create_modbus_response(mock_inverter_model_registers)
        else:
            return create_modbus_response([0] * count)

    mock_client.read_holding_registers = AsyncMock(side_effect=mock_read)

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await async_setup_entry(hass, mock_config_entry)
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
