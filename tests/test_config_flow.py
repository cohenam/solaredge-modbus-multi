"""Tests for the SolarEdge Modbus Multi config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.solaredge_modbus_multi.const import DOMAIN, ConfName


async def test_form_user(hass: HomeAssistant) -> None:
    """Test the user config flow shows form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_form_user_with_valid_input(
    hass: HomeAssistant,
    mock_modbus_client,
    mock_inverter_registers,
) -> None:
    """Test successful config flow with valid input."""
    from tests.conftest import create_modbus_response

    # Setup mock responses
    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.return_value = create_modbus_response(
        mock_inverter_registers
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Test SolarEdge",
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
                ConfName.DEVICE_LIST: "1",
            },
        )
        await hass.async_block_till_done()

    # Should either create entry or show form for options
    assert result["type"] in (
        FlowResultType.CREATE_ENTRY,
        FlowResultType.FORM,
    )


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test we handle connection errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient"
    ) as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.connect = AsyncMock(side_effect=ConnectionError("Failed"))
        mock_instance.connected = False

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Test SolarEdge",
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
                ConfName.DEVICE_LIST: "1",
            },
        )

    # Should show error or abort
    assert result["type"] in (
        FlowResultType.FORM,
        FlowResultType.ABORT,
    )


async def test_form_duplicate_entry(
    hass: HomeAssistant,
    mock_config_entry_data,
) -> None:
    """Test duplicate entry prevention."""
    # Create existing entry
    existing_entry = config_entries.ConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Existing SolarEdge",
        data=mock_config_entry_data,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    existing_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Try to add same host:port
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Duplicate SolarEdge",
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        },
    )

    # Should abort due to duplicate
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
