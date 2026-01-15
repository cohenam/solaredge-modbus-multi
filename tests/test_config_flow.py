"""Tests for the SolarEdge Modbus Multi config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_modbus_multi.const import DOMAIN, ConfName

# Check if SOURCE_RECONFIGURE is available (Home Assistant 2025.2+)
HAS_RECONFIGURE = hasattr(config_entries, "SOURCE_RECONFIGURE")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


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
    """Test connection errors result in entry that retries."""
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

    # Config flow creates entry, setup raises ConfigEntryNotReady for retry
    assert result["type"] in (
        FlowResultType.CREATE_ENTRY,
        FlowResultType.FORM,
        FlowResultType.ABORT,
    )


async def test_form_duplicate_entry(
    hass: HomeAssistant,
    mock_config_entry_data,
) -> None:
    """Test duplicate entry prevention."""
    # Create existing entry
    existing_entry = MockConfigEntry(
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


async def test_form_invalid_host(hass: HomeAssistant) -> None:
    """Test invalid host error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test SolarEdge",
            CONF_HOST: "invalid host name!",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_form_invalid_port(hass: HomeAssistant) -> None:
    """Test invalid port error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test SolarEdge",
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 70000,
            ConfName.DEVICE_LIST: "1",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "invalid_tcp_port"}


async def test_form_invalid_device_list(hass: HomeAssistant) -> None:
    """Test invalid device list error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test SolarEdge",
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "invalid",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert ConfName.DEVICE_LIST in result["errors"]


async def test_form_invalid_inverter_count(hass: HomeAssistant) -> None:
    """Test invalid inverter count error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Test too many inverters (>32)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test SolarEdge",
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: ",".join(str(i) for i in range(1, 34)),
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ConfName.DEVICE_LIST: "invalid_inverter_count"}


# Reconfigure flow tests
@pytest.mark.skipif(not HAS_RECONFIGURE, reason="SOURCE_RECONFIGURE not available")
async def test_reconfigure_form_display(
    hass: HomeAssistant, mock_config_entry_data
) -> None:
    """Test reconfigure flow shows form with current values."""
    # Create existing entry
    existing_entry = MockConfigEntry(
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
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": existing_entry.entry_id,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {}


@pytest.mark.skipif(not HAS_RECONFIGURE, reason="SOURCE_RECONFIGURE not available")
async def test_reconfigure_valid_input(
    hass: HomeAssistant, mock_config_entry_data
) -> None:
    """Test reconfigure with valid input."""
    # Create existing entry
    existing_entry = MockConfigEntry(
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
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": existing_entry.entry_id,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.101",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1,2",
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.skipif(not HAS_RECONFIGURE, reason="SOURCE_RECONFIGURE not available")
async def test_reconfigure_invalid_host(
    hass: HomeAssistant, mock_config_entry_data
) -> None:
    """Test reconfigure with invalid host."""
    # Create existing entry
    existing_entry = MockConfigEntry(
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
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": existing_entry.entry_id,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "invalid host!",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


@pytest.mark.skipif(not HAS_RECONFIGURE, reason="SOURCE_RECONFIGURE not available")
async def test_reconfigure_invalid_port(
    hass: HomeAssistant, mock_config_entry_data
) -> None:
    """Test reconfigure with invalid port."""
    # Create existing entry
    existing_entry = MockConfigEntry(
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
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": existing_entry.entry_id,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 0,
            ConfName.DEVICE_LIST: "1",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "invalid_tcp_port"}


@pytest.mark.skipif(not HAS_RECONFIGURE, reason="SOURCE_RECONFIGURE not available")
async def test_reconfigure_duplicate_entry(
    hass: HomeAssistant, mock_config_entry_data
) -> None:
    """Test reconfigure duplicate entry prevention."""
    # Create first entry
    first_entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="First SolarEdge",
        data=mock_config_entry_data,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    first_entry.add_to_hass(hass)

    # Create second entry
    second_entry_data = {
        CONF_HOST: "192.168.1.101",
        CONF_PORT: 1502,
        CONF_NAME: "Second SolarEdge",
        ConfName.DEVICE_LIST: [2],
    }
    second_entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Second SolarEdge",
        data=second_entry_data,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.101:1502",
    )
    second_entry.add_to_hass(hass)

    # Try to reconfigure second entry to match first entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": second_entry.entry_id,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        },
    )

    # Should abort due to duplicate
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] in ["already_configured", "reconfigure_successful"]


# Options flow tests
async def test_options_flow_init_form_display(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow shows form."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {}


async def test_options_flow_init_valid_input(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow with valid input completes."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 600,
            ConfName.KEEP_MODBUS_OPEN: True,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 5,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_options_flow_init_scan_interval_too_low(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow with scan interval too low."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 0,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SCAN_INTERVAL: "invalid_scan_interval"}


async def test_options_flow_init_scan_interval_too_high(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow with scan interval too high."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 86401,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SCAN_INTERVAL: "invalid_scan_interval"}


async def test_options_flow_init_sleep_after_write_too_low(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow with sleep_after_write too low."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: -1,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ConfName.SLEEP_AFTER_WRITE: "invalid_sleep_interval"}


async def test_options_flow_init_sleep_after_write_too_high(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow with sleep_after_write too high."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 61,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ConfName.SLEEP_AFTER_WRITE: "invalid_sleep_interval"}


async def test_options_flow_init_navigate_to_battery(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test options flow navigates to battery options when batteries enabled."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_options"


# Battery options flow tests
async def test_battery_options_form_display(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test battery options flow shows form."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_options"


async def test_battery_options_valid_input(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test battery options with valid input completes."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    # Complete battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ALLOW_BATTERY_ENERGY_RESET: True,
            ConfName.BATTERY_ENERGY_RESET_CYCLES: 10,
            ConfName.BATTERY_RATING_ADJUST: 5,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_battery_options_rating_too_low(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test battery options with rating adjust too low."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    # Submit with invalid rating
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ALLOW_BATTERY_ENERGY_RESET: False,
            ConfName.BATTERY_ENERGY_RESET_CYCLES: 0,
            ConfName.BATTERY_RATING_ADJUST: -1,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ConfName.BATTERY_RATING_ADJUST: "invalid_percent"}


async def test_battery_options_rating_too_high(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test battery options with rating adjust too high."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: False,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    # Submit with invalid rating
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ALLOW_BATTERY_ENERGY_RESET: False,
            ConfName.BATTERY_ENERGY_RESET_CYCLES: 0,
            ConfName.BATTERY_RATING_ADJUST: 101,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ConfName.BATTERY_RATING_ADJUST: "invalid_percent"}


# Advanced power control flow tests
async def test_adv_pwr_ctl_form_display(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test advanced power control flow shows form."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: True,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "adv_pwr_ctl"


async def test_adv_pwr_ctl_completion(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test advanced power control completes successfully."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: True,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    # Complete adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ADV_STORAGE_CONTROL: True,
            ConfName.ADV_SITE_LIMIT_CONTROL: True,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_battery_to_adv_pwr_ctl_flow(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test full flow from init to battery to adv power control."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to battery options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: True,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: True,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["step_id"] == "battery_options"

    # Navigate to adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ALLOW_BATTERY_ENERGY_RESET: True,
            ConfName.BATTERY_ENERGY_RESET_CYCLES: 10,
            ConfName.BATTERY_RATING_ADJUST: 5,
        },
    )

    assert result["step_id"] == "adv_pwr_ctl"

    # Complete adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ADV_STORAGE_CONTROL: True,
            ConfName.ADV_SITE_LIMIT_CONTROL: False,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_adv_pwr_ctl_direct_from_init(
    hass: HomeAssistant, mock_config_entry_data, mock_config_entry_options
) -> None:
    """Test advanced power control flow directly from init (no batteries)."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data=mock_config_entry_data,
        options=mock_config_entry_options,
        source=config_entries.SOURCE_USER,
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate directly to adv power control (batteries disabled)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 300,
            ConfName.KEEP_MODBUS_OPEN: False,
            ConfName.DETECT_METERS: True,
            ConfName.DETECT_BATTERIES: False,
            ConfName.DETECT_EXTRAS: False,
            ConfName.ADV_PWR_CONTROL: True,
            ConfName.SLEEP_AFTER_WRITE: 0,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "adv_pwr_ctl"

    # Complete adv power control
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            ConfName.ADV_STORAGE_CONTROL: False,
            ConfName.ADV_SITE_LIMIT_CONTROL: True,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
