"""Tests for the SolarEdge Modbus Multi hub."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solaredge_modbus_multi.const import DOMAIN
from custom_components.solaredge_modbus_multi.hub import (
    ModbusReadError,
    SolarEdgeModbusMultiHub,
)


@pytest.fixture
def mock_hub(hass, mock_config_entry_data, mock_config_entry_options):
    """Create a mock hub instance."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    hub = SolarEdgeModbusMultiHub(
        hass,
        entry_id="test_entry",
        entry_data=mock_config_entry_data,
        entry_options=mock_config_entry_options,
    )
    return hub


async def test_hub_connect(mock_hub, mock_modbus_client) -> None:
    """Test hub connection."""
    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()

    assert mock_hub._client is not None
    mock_modbus_client.return_value.connect.assert_called_once()


async def test_hub_disconnect(mock_hub, mock_modbus_client) -> None:
    """Test hub disconnection."""
    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        mock_hub.disconnect()

    mock_modbus_client.return_value.close.assert_called_once()


async def test_hub_disconnect_clear_client(mock_hub, mock_modbus_client) -> None:
    """Test hub disconnection with client clearing."""
    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        mock_hub.disconnect(clear_client=True)

    assert mock_hub._client is None


async def test_hub_read_registers(
    mock_hub, mock_modbus_client, mock_inverter_registers
) -> None:
    """Test reading modbus registers."""
    from tests.conftest import create_modbus_response

    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.return_value = create_modbus_response(
        mock_inverter_registers
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        result = await mock_hub.modbus_read_holding_registers(
            unit=1, address=40000, rcount=len(mock_inverter_registers)
        )

    assert result.registers == mock_inverter_registers


async def test_hub_read_registers_error(mock_hub, mock_modbus_client) -> None:
    """Test reading modbus registers with error response."""
    mock_client = mock_modbus_client.return_value
    error_response = MagicMock()
    error_response.isError.return_value = True
    mock_client.read_holding_registers.return_value = error_response

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()

        with pytest.raises(ModbusReadError):
            await mock_hub.modbus_read_holding_registers(
                unit=1, address=40000, rcount=10
            )


async def test_hub_modbus_lock_prevents_race_condition(
    mock_hub, mock_modbus_client, mock_inverter_registers
) -> None:
    """Test that modbus lock prevents race conditions."""
    from tests.conftest import create_modbus_response

    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.return_value = create_modbus_response(
        mock_inverter_registers
    )

    # Track order of operations
    operation_order = []

    original_read = mock_client.read_holding_registers

    async def tracked_read(*args, **kwargs):
        operation_order.append(f"start_{kwargs.get('address', args[0] if args else 0)}")
        await asyncio.sleep(0.01)  # Simulate I/O delay
        result = await original_read(*args, **kwargs)
        operation_order.append(f"end_{kwargs.get('address', args[0] if args else 0)}")
        return result

    mock_client.read_holding_registers = tracked_read

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()

        # Run two reads concurrently
        task1 = asyncio.create_task(
            mock_hub.modbus_read_holding_registers(
                unit=1, address=40000, rcount=len(mock_inverter_registers)
            )
        )
        task2 = asyncio.create_task(
            mock_hub.modbus_read_holding_registers(
                unit=1, address=40100, rcount=len(mock_inverter_registers)
            )
        )

        await asyncio.gather(task1, task2)

    # With proper locking, operations should complete one at a time
    # (start_X, end_X, start_Y, end_Y) not interleaved
    assert len(operation_order) == 4
    # First operation should complete before second starts
    assert operation_order[1].startswith("end_")
    assert operation_order[2].startswith("start_")


async def test_hub_shutdown(mock_hub, mock_modbus_client) -> None:
    """Test hub shutdown."""
    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        await mock_hub.shutdown()

    assert mock_hub.online is False
    assert mock_hub._client is None


async def test_hub_properties(mock_hub) -> None:
    """Test hub property accessors."""
    assert mock_hub.name == "Test SolarEdge"
    assert mock_hub.hub_host == "192.168.1.100"
    assert mock_hub.hub_port == 1502
    assert mock_hub.keep_modbus_open is False
    assert mock_hub.number_of_inverters == 1
