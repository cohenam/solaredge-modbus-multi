"""Tests for the hardware-write gate.

Writes are opt-in. With the gate off no write-capable entity is created and
the hub refuses a write outright, before it can reach the transport.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.solaredge_modbus_multi.button import (
    SolarEdgeCommitControlSettings,
    SolarEdgeDefaultControlSettings,
    SolarEdgeRefreshButton,
)
from custom_components.solaredge_modbus_multi.button import (
    async_setup_entry as button_setup_entry,
)
from custom_components.solaredge_modbus_multi.const import DOMAIN, ConfName
from custom_components.solaredge_modbus_multi.hub import SolarEdgeModbusMultiHub
from custom_components.solaredge_modbus_multi.number import (
    async_setup_entry as number_setup_entry,
)
from custom_components.solaredge_modbus_multi.select import (
    async_setup_entry as select_setup_entry,
)
from custom_components.solaredge_modbus_multi.switch import (
    async_setup_entry as switch_setup_entry,
)


def build_hub(hass, entry_data, entry_options, *, allow_writes):
    """Build a real hub with the write gate in a known state."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    options = {**entry_options}
    if allow_writes is None:
        options.pop(ConfName.ALLOW_HARDWARE_WRITES, None)
    else:
        options[ConfName.ALLOW_HARDWARE_WRITES] = allow_writes

    return SolarEdgeModbusMultiHub(
        hass,
        entry_id="test_entry",
        entry_data=entry_data,
        entry_options=options,
    )


@pytest.fixture
def platform_entry(mock_coordinator_stub):
    """A config entry whose runtime_data carries a mock hub and coordinator."""

    def _build(*, allow_writes: bool, detect_extras: bool = True):
        hub = MagicMock()
        hub.option_allow_hardware_writes = allow_writes
        hub.option_site_limit_control = True
        hub.option_storage_control = True
        hub.option_detect_extras = detect_extras

        inverter = MagicMock()
        inverter.advanced_power_control = True
        inverter.global_power_control = True
        inverter.decoded_storage_control = {"control_mode": 0}
        inverter.has_battery = True
        inverter.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        hub.inverters = [inverter]

        entry = MagicMock()
        entry.entry_id = "test_entry_123"
        entry.data = {"name": "Test SolarEdge"}
        entry.runtime_data = SimpleNamespace(hub=hub, coordinator=mock_coordinator_stub)
        return entry

    return _build


@pytest.fixture
def mock_coordinator_stub():
    """Minimal coordinator double for entity construction."""
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock()
    coordinator.data = {}
    return coordinator


# --- the option itself --------------------------------------------------------


def test_hardware_writes_default_off(
    hass, mock_config_entry_data, mock_config_entry_options
) -> None:
    """An entry with no stored preference must not permit writes."""
    hub = build_hub(
        hass, mock_config_entry_data, mock_config_entry_options, allow_writes=None
    )

    assert hub.option_allow_hardware_writes is False


def test_hardware_writes_reflects_option(
    hass, mock_config_entry_data, mock_config_entry_options
) -> None:
    """The stored option drives the property both ways."""
    assert (
        build_hub(
            hass, mock_config_entry_data, mock_config_entry_options, allow_writes=True
        ).option_allow_hardware_writes
        is True
    )
    assert (
        build_hub(
            hass, mock_config_entry_data, mock_config_entry_options, allow_writes=False
        ).option_allow_hardware_writes
        is False
    )


# --- the central refusal ------------------------------------------------------


async def test_write_refused_never_reaches_transport(
    hass,
    mock_config_entry_data,
    mock_config_entry_options,
    mock_modbus_client,
) -> None:
    """A refused write must not touch the modbus client at all.

    Entity suppression alone is not enough: a restored entity or a service
    call could still call through, so the refusal lives in the hub.
    """
    hub = build_hub(
        hass, mock_config_entry_data, mock_config_entry_options, allow_writes=False
    )
    mock_client = mock_modbus_client.return_value

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await hub.connect()

        with pytest.raises(HomeAssistantError, match="Hardware writes are disabled"):
            await hub.write_registers(unit=1, address=61696, payload=[1])

    mock_client.write_registers.assert_not_called()


async def test_refused_write_does_not_disturb_poll_state(
    hass,
    mock_config_entry_data,
    mock_config_entry_options,
    mock_modbus_client,
) -> None:
    """Refusal happens before any write bookkeeping runs."""
    hub = build_hub(
        hass, mock_config_entry_data, mock_config_entry_options, allow_writes=False
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await hub.connect()

        with pytest.raises(HomeAssistantError):
            await hub.write_registers(unit=1, address=61700, payload=[0, 1])

    assert hub.has_write is None
    assert hub._slow_poll_requests == 0
    assert hub.uncommitted_power_settings == []


async def test_write_allowed_when_enabled(
    hass,
    mock_config_entry_data,
    mock_config_entry_options,
    mock_modbus_client,
) -> None:
    """With the gate on the write reaches the client unchanged."""
    hub = build_hub(
        hass, mock_config_entry_data, mock_config_entry_options, allow_writes=True
    )
    mock_client = mock_modbus_client.return_value
    success = MagicMock()
    success.isError.return_value = False
    mock_client.write_registers.return_value = success

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await hub.connect()
        await hub.write_registers(unit=1, address=61441, payload=[100])

    mock_client.write_registers.assert_called_once()


# --- entity suppression per platform -----------------------------------------


@pytest.mark.parametrize(
    "setup_entry",
    [number_setup_entry, select_setup_entry, switch_setup_entry],
    ids=["number", "select", "switch"],
)
async def test_write_platforms_add_nothing_when_disabled(
    hass: HomeAssistant, platform_entry, setup_entry
) -> None:
    """Every entity on these platforms writes, so none may be created."""
    async_add_entities = MagicMock()

    await setup_entry(hass, platform_entry(allow_writes=False), async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.parametrize(
    "setup_entry",
    [number_setup_entry, select_setup_entry, switch_setup_entry],
    ids=["number", "select", "switch"],
)
async def test_write_platforms_populate_when_enabled(
    hass: HomeAssistant, platform_entry, setup_entry
) -> None:
    """Regression guard: the gate is the only thing suppressing them."""
    async_add_entities = MagicMock()

    await setup_entry(hass, platform_entry(allow_writes=True), async_add_entities)

    async_add_entities.assert_called_once()
    assert len(async_add_entities.call_args[0][0]) > 0


async def test_button_keeps_only_refresh_when_disabled(
    hass: HomeAssistant, platform_entry
) -> None:
    """Refresh re-reads and never writes, so it survives the gate."""
    async_add_entities = MagicMock()

    await button_setup_entry(
        hass, platform_entry(allow_writes=False), async_add_entities
    )

    entities = async_add_entities.call_args[0][0]
    assert [type(entity) for entity in entities] == [SolarEdgeRefreshButton]


async def test_button_adds_control_buttons_when_enabled(
    hass: HomeAssistant, platform_entry
) -> None:
    """With the gate on, the commit and restore buttons come back."""
    async_add_entities = MagicMock()

    await button_setup_entry(
        hass, platform_entry(allow_writes=True), async_add_entities
    )

    entities = async_add_entities.call_args[0][0]
    assert [type(entity) for entity in entities] == [
        SolarEdgeRefreshButton,
        SolarEdgeCommitControlSettings,
        SolarEdgeDefaultControlSettings,
    ]
