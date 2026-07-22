"""Tests for tiered polling: slow blocks read every Nth cycle."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.solaredge_modbus_multi.const import (
    DOMAIN,
    ConfName,
    ModbusExceptions,
)
from custom_components.solaredge_modbus_multi.hub import (
    SolarEdgeInverter,
    SolarEdgeModbusMultiHub,
)
from tests.conftest import create_exception_response, create_modbus_response

SLOW_ADDRESSES = {61440, 61696, 61782, 57344, 57362, 57348}
FAST_ADDRESSES = {40044, 40113}


@pytest.fixture
def mock_hub(hass, mock_config_entry_data, mock_config_entry_options):
    """Hub with all control features enabled and a 3-cycle multiplier."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    options = {
        **mock_config_entry_options,
        ConfName.DETECT_EXTRAS: True,
        ConfName.ADV_STORAGE_CONTROL: True,
        ConfName.ADV_SITE_LIMIT_CONTROL: True,
        ConfName.SLOW_POLL_MULTIPLIER: 3,
    }
    return SolarEdgeModbusMultiHub(
        hass,
        entry_id="test_entry",
        entry_data=mock_config_entry_data,
        entry_options=options,
    )


def _recording_side_effect(
    mock_inverter_registers, mock_inverter_model_registers, seen: list[int]
):
    def side_effect(*args, **kwargs):
        address = kwargs.get("address", args[0] if args else 0)
        count = kwargs.get("count", args[1] if len(args) > 1 else 10)
        seen.append(address)
        if address == 40000:
            return create_modbus_response(mock_inverter_registers)
        if address == 40044:
            return create_modbus_response([0] * 25 + mock_inverter_model_registers)
        if address == 40121:
            return create_exception_response(ModbusExceptions.IllegalAddress)
        return create_modbus_response([0] * count)

    return side_effect


async def test_slow_blocks_follow_poll_tiers(
    mock_hub, mock_modbus_client, mock_inverter_registers, mock_inverter_model_registers
) -> None:
    """Slow blocks are read when due (or undetected) and skipped otherwise."""
    seen: list[int] = []
    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.side_effect = _recording_side_effect(
        mock_inverter_registers, mock_inverter_model_registers, seen
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        inverter = SolarEdgeInverter(device_id=1, hub=mock_hub)
        await inverter.init_device()

        # First poll: slow_poll_due default True -> everything is read
        seen.clear()
        mock_hub.slow_poll_due = True
        await inverter.read_modbus_data()
        assert SLOW_ADDRESSES <= set(seen)
        assert FAST_ADDRESSES <= set(seen)

        # Off-cycle poll: features already detected -> slow blocks skipped
        seen.clear()
        mock_hub.slow_poll_due = False
        await inverter.read_modbus_data()
        assert not (SLOW_ADDRESSES & set(seen))
        assert FAST_ADDRESSES <= set(seen)

        # Values from the last slow poll are retained for entities
        assert "E_Site_Limit" in inverter.decoded_model
        assert "AdvPwrCtrlEn" in inverter.decoded_model

        # Due again -> slow blocks re-read
        seen.clear()
        mock_hub.slow_poll_due = True
        await inverter.read_modbus_data()
        assert SLOW_ADDRESSES <= set(seen)


async def test_slow_poll_cycle_counter(mock_hub) -> None:
    """slow_poll_due follows the multiplier across refresh cycles."""
    mock_hub.initalized = True
    mock_hub._keep_modbus_open = True
    mock_hub._client = MagicMock()
    mock_hub._client.connected = True

    due_pattern = []
    for _ in range(7):
        await mock_hub.async_refresh_modbus_data()
        due_pattern.append(mock_hub.slow_poll_due)

    # Multiplier 3: due on cycles 0, 3 and 6
    assert due_pattern == [True, False, False, True, False, False, True]


async def test_write_forces_slow_poll(mock_hub) -> None:
    """A write to control registers forces a slow poll on the next cycle."""
    mock_hub.initalized = True
    mock_hub._keep_modbus_open = True
    mock_hub._client = MagicMock()
    mock_hub._client.connected = True

    # Cycle 0 is always a slow poll; cycle 1 is not (multiplier 3)
    await mock_hub.async_refresh_modbus_data()
    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is False

    mock_hub._force_slow_poll = True

    # Cycle 2 would be off-cycle, but the write forces a slow poll
    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is True

    # One-shot: cycle 4 (after natural slow cycle 3) is off-cycle again
    await mock_hub.async_refresh_modbus_data()
    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is False


async def test_multiplier_of_one_polls_everything(
    hass, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Multiplier 1 restores read-everything-every-cycle behavior."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    hub = SolarEdgeModbusMultiHub(
        hass,
        entry_id="test_entry",
        entry_data=mock_config_entry_data,
        entry_options={
            **mock_config_entry_options,
            ConfName.SLOW_POLL_MULTIPLIER: 1,
        },
    )
    hub.initalized = True
    hub._keep_modbus_open = True
    hub._client = MagicMock()
    hub._client.connected = True

    for _ in range(4):
        await hub.async_refresh_modbus_data()
        assert hub.slow_poll_due is True
