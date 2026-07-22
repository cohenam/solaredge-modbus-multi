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

    mock_hub._slow_poll_requests += 1

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


async def test_disabled_slow_block_drops_stale_values(
    mock_hub, mock_modbus_client, mock_inverter_registers, mock_inverter_model_registers
) -> None:
    """A block that stops responding must drop its values, not keep them."""
    seen: list[int] = []
    mock_client = mock_modbus_client.return_value
    side_effect = _recording_side_effect(
        mock_inverter_registers, mock_inverter_model_registers, seen
    )
    mock_client.read_holding_registers.side_effect = side_effect

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        inverter = SolarEdgeInverter(device_id=1, hub=mock_hub)
        await inverter.init_device()

        mock_hub.slow_poll_due = True
        await inverter.read_modbus_data()
        assert "E_Site_Limit" in inverter.decoded_model
        assert "AdvPwrCtrlEn" in inverter.decoded_model
        assert "I_RRCR" in inverter.decoded_model

        # Control blocks start failing on the next slow poll
        def failing_side_effect(*args, **kwargs):
            address = kwargs.get("address", args[0] if args else 0)
            if address in SLOW_ADDRESSES:
                return create_exception_response(ModbusExceptions.IllegalAddress)
            return side_effect(*args, **kwargs)

        mock_client.read_holding_registers.side_effect = failing_side_effect
        mock_hub.slow_poll_due = True
        await inverter.read_modbus_data()

        assert inverter.site_limit_control is False
        assert inverter.advanced_power_control is False
        assert inverter.global_power_control is False
        for key in ("E_Site_Limit", "E_Lim_Ctl_Mode", "E_Lim_Ctl", "Ext_Prod_Max"):
            assert key not in inverter.decoded_model
        assert "AdvPwrCtrlEn" not in inverter.decoded_model
        assert "PwrFrqDeratingResetTime" not in inverter.decoded_model
        assert "I_RRCR" not in inverter.decoded_model


async def test_failed_poll_preserves_forced_slow_poll(mock_hub) -> None:
    """A failed refresh must not consume a pending forced slow poll."""
    from unittest.mock import AsyncMock

    from custom_components.solaredge_modbus_multi.hub import (
        DataUpdateFailed,
        ModbusReadError,
    )

    mock_hub.initalized = True
    mock_hub._keep_modbus_open = True
    mock_hub._client = MagicMock()
    mock_hub._client.connected = True

    # Two successful cycles (0 and 1); cycle 2 would be off-cycle
    await mock_hub.async_refresh_modbus_data()
    await mock_hub.async_refresh_modbus_data()
    cycle_before = mock_hub._poll_cycle

    mock_hub._slow_poll_requests += 1

    failing_inverter = MagicMock()
    failing_inverter.read_modbus_data = AsyncMock(side_effect=ModbusReadError("boom"))
    mock_hub.inverters = [failing_inverter]

    with pytest.raises(DataUpdateFailed):
        await mock_hub.async_refresh_modbus_data()

    # Failure must not commit the cycle or consume the forced slow poll
    assert mock_hub._slow_poll_requests == 1
    assert mock_hub._poll_cycle == cycle_before

    # Next (successful) refresh performs the forced slow poll
    mock_hub.inverters = []
    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is True
    assert mock_hub._slow_poll_requests == 0


async def test_write_registers_requests_slow_poll(mock_hub, mock_modbus_client) -> None:
    """A successful write requests a slow-block re-read."""
    mock_hub._sleep_after_write = 0
    mock_client = mock_modbus_client.return_value
    mock_client.write_registers.return_value = create_modbus_response([])

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        await mock_hub.write_registers(unit=1, address=57348, payload=[1])

    assert mock_hub._slow_poll_requests == 1


async def test_write_during_refresh_keeps_slow_poll_request(mock_hub) -> None:
    """A write landing mid-refresh must not lose its forced slow poll.

    Device-level locking allows write_registers to run between device
    polls of an in-flight refresh. That refresh already snapshotted its
    tier, so the write's request must survive the refresh completing.
    """
    from unittest.mock import AsyncMock

    mock_hub.initalized = True
    mock_hub._keep_modbus_open = True
    mock_hub._client = MagicMock()
    mock_hub._client.connected = True

    # Cycles 0 and 1: cycle 2 will be an off-cycle (multiplier 3)
    await mock_hub.async_refresh_modbus_data()
    await mock_hub.async_refresh_modbus_data()

    async def write_lands_between_device_polls():
        # Same effect write_registers has on tier state (covered by
        # test_write_registers_requests_slow_poll), at the exact
        # interleaving point: during an in-flight refresh.
        mock_hub._slow_poll_requests += 1

    inverter = MagicMock()
    inverter.read_modbus_data = AsyncMock(side_effect=write_lands_between_device_polls)
    inverter.set_last_update = MagicMock()
    mock_hub.inverters = [inverter]

    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is False  # this refresh stayed fast
    assert mock_hub._slow_poll_requests == 1  # request not clobbered

    # The very next refresh serves the write's forced slow poll
    mock_hub.inverters = []
    await mock_hub.async_refresh_modbus_data()
    assert mock_hub.slow_poll_due is True
    assert mock_hub._slow_poll_requests == 0
