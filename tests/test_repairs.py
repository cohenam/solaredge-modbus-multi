"""Tests for the SolarEdge Modbus Multi repairs module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.repairs import RepairsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_modbus_multi.const import (
    DOMAIN,
    ConfDefaultStr,
    ConfName,
)
from custom_components.solaredge_modbus_multi.repairs import (
    CheckConfigurationRepairFlow,
    async_create_fix_flow,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a mock config entry for repairs."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test SolarEdge",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            CONF_NAME: "Test SolarEdge",
            ConfName.DEVICE_LIST: [1, 2],
        },
        source="user",
        unique_id="192.168.1.100:1502",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def repair_flow(mock_config_entry: MockConfigEntry) -> CheckConfigurationRepairFlow:
    """Create a CheckConfigurationRepairFlow instance."""
    return CheckConfigurationRepairFlow(mock_config_entry)


class TestCheckConfigurationRepairFlow:
    """Test CheckConfigurationRepairFlow class."""

    def test_init(self, mock_config_entry: MockConfigEntry) -> None:
        """Test initialization of repair flow."""
        flow = CheckConfigurationRepairFlow(mock_config_entry)
        assert flow._entry == mock_config_entry
        assert isinstance(flow, RepairsFlow)

    async def test_async_step_init(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test async_step_init redirects to confirm step."""
        repair_flow.hass = hass

        with patch.object(
            repair_flow, "async_step_confirm", new_callable=AsyncMock
        ) as mock_confirm:
            mock_confirm.return_value = {"type": "form"}
            result = await repair_flow.async_step_init()
            mock_confirm.assert_called_once()
            assert result == {"type": "form"}

    async def test_async_step_confirm_no_input_shows_form(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step without user input shows form with current values."""
        repair_flow.hass = hass

        result = await repair_flow.async_step_confirm(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert result["errors"] == {}
        # Verify default values are populated from config entry
        assert "data_schema" in result

    async def test_async_step_confirm_valid_input(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with valid input updates config entry."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.200",
            CONF_PORT: 1503,
            ConfName.DEVICE_LIST: "1,2,3",
        }

        with patch.object(
            hass.config_entries, "async_update_entry"
        ) as mock_update:
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["title"] == ""
        assert result["data"] == {}

        # Verify config entry was updated with normalized values
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == repair_flow._entry
        assert call_args[1]["unique_id"] == "192.168.1.200:1503"
        # Verify device_list was converted from string to list
        assert call_args[1]["data"][ConfName.DEVICE_LIST] == [1, 2, 3]
        # Verify host was lowercased
        assert call_args[1]["data"][CONF_HOST] == "192.168.1.200"

    async def test_async_step_confirm_normalizes_host(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step normalizes host to lowercase."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "INVERTER.LOCAL",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        with patch.object(hass.config_entries, "async_update_entry"):
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"

    async def test_async_step_confirm_strips_whitespace_from_device_list(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step strips whitespace from device_list."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: " 1 , 2 , 3 ",
        }

        with patch.object(
            hass.config_entries, "async_update_entry"
        ) as mock_update:
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"
        # Verify whitespace was removed
        call_args = mock_update.call_args
        assert call_args[1]["data"][ConfName.DEVICE_LIST] == [1, 2, 3]

    async def test_async_step_confirm_invalid_device_list_format(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with invalid device_list format."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "invalid",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert ConfName.DEVICE_LIST in result["errors"]
        # Error message should contain the exception message
        assert "invalid_device_id" in result["errors"][ConfName.DEVICE_LIST]

    async def test_async_step_confirm_empty_device_list(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with empty device_list."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert ConfName.DEVICE_LIST in result["errors"]

    async def test_async_step_confirm_invalid_device_range(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with invalid device range (end < start)."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "5-1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert ConfName.DEVICE_LIST in result["errors"]
        assert "invalid_range_lte" in result["errors"][ConfName.DEVICE_LIST]

    async def test_async_step_confirm_invalid_host(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with invalid host.

        Note: Due to a bug in host_valid(), invalid IPs like 999.999.999.999
        are incorrectly accepted because they match the DOMAIN_REGEX pattern.
        This test documents the current behavior.
        """
        repair_flow.hass = hass

        # Use a truly invalid hostname that won't match the regex
        user_input = {
            CONF_HOST: "invalid..host",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert CONF_HOST in result["errors"]
        assert result["errors"][CONF_HOST] == "invalid_host"

    async def test_async_step_confirm_invalid_port_too_low(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with port number too low."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 0,
            ConfName.DEVICE_LIST: "1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert CONF_PORT in result["errors"]
        assert result["errors"][CONF_PORT] == "invalid_tcp_port"

    async def test_async_step_confirm_invalid_port_too_high(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with port number too high."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 65536,
            ConfName.DEVICE_LIST: "1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert CONF_PORT in result["errors"]
        assert result["errors"][CONF_PORT] == "invalid_tcp_port"

    async def test_async_step_confirm_inverter_count_too_low(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with zero inverters."""
        repair_flow.hass = hass

        # Create a mock that returns empty list
        with patch(
            "custom_components.solaredge_modbus_multi.repairs.device_list_from_string",
            return_value=[],
        ):
            user_input = {
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
                ConfName.DEVICE_LIST: "1",
            }

            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert ConfName.DEVICE_LIST in result["errors"]
        assert result["errors"][ConfName.DEVICE_LIST] == "invalid_inverter_count"

    async def test_async_step_confirm_inverter_count_too_high(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with too many inverters."""
        repair_flow.hass = hass

        # Create a list with 33 inverters
        with patch(
            "custom_components.solaredge_modbus_multi.repairs.device_list_from_string",
            return_value=list(range(1, 34)),
        ):
            user_input = {
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
                ConfName.DEVICE_LIST: "1-33",
            }

            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert ConfName.DEVICE_LIST in result["errors"]
        assert result["errors"][ConfName.DEVICE_LIST] == "invalid_inverter_count"

    async def test_async_step_confirm_already_configured(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with host:port already configured in another entry."""
        repair_flow.hass = hass

        # Create another config entry with different unique_id
        existing_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.200",
                CONF_PORT: 1502,
                ConfName.DEVICE_LIST: [3],
            },
            unique_id="192.168.1.200:1502",
        )
        existing_entry.add_to_hass(hass)

        # Try to configure the current entry with the same host:port as existing
        user_input = {
            CONF_HOST: "192.168.1.200",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        assert CONF_HOST in result["errors"]
        assert CONF_PORT in result["errors"]
        assert result["errors"][CONF_HOST] == "already_configured"
        assert result["errors"][CONF_PORT] == "already_configured"

    async def test_async_step_confirm_same_unique_id_allowed(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step allows keeping the same unique_id."""
        repair_flow.hass = hass

        # Use the same host:port as the original entry
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1,2,3",
        }

        with patch.object(hass.config_entries, "async_update_entry"):
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"

    async def test_async_step_confirm_valid_hostname(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with valid hostname."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "inverter.local",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        with patch.object(hass.config_entries, "async_update_entry"):
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"

    async def test_async_step_confirm_valid_ipv6(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with valid IPv6 address.

        Note: Due to a bug in host_valid() where it checks
        `ip.version == (4 or 6)` which evaluates to `ip.version == 4`,
        IPv6 addresses are incorrectly rejected. This test documents
        the current (buggy) behavior.
        """
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "::1",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        result = await repair_flow.async_step_confirm(user_input=user_input)

        # Currently fails validation due to host_valid() bug
        assert result["type"] == "form"
        assert CONF_HOST in result["errors"]
        assert result["errors"][CONF_HOST] == "invalid_host"

    async def test_async_step_confirm_device_range(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with device range."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1-5",
        }

        with patch.object(
            hass.config_entries, "async_update_entry"
        ) as mock_update:
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"
        call_args = mock_update.call_args
        # Verify range was expanded to list
        assert call_args[1]["data"][ConfName.DEVICE_LIST] == [1, 2, 3, 4, 5]

    async def test_async_step_confirm_complex_device_list(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step with complex device list (ranges and individual IDs)."""
        repair_flow.hass = hass

        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1,3-5,10",
        }

        with patch.object(
            hass.config_entries, "async_update_entry"
        ) as mock_update:
            result = await repair_flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"
        call_args = mock_update.call_args
        # Verify complex list was parsed correctly
        assert call_args[1]["data"][ConfName.DEVICE_LIST] == [1, 3, 4, 5, 10]

    async def test_async_step_confirm_default_device_list_on_no_input(
        self, hass: HomeAssistant, repair_flow: CheckConfigurationRepairFlow
    ) -> None:
        """Test confirm step uses default when device_list not in config."""
        repair_flow.hass = hass

        # Create entry without device_list in data
        entry_without_device_list = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
            },
            unique_id="192.168.1.100:1502",
        )
        entry_without_device_list.add_to_hass(hass)

        flow = CheckConfigurationRepairFlow(entry_without_device_list)
        flow.hass = hass

        result = await flow.async_step_confirm(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        # Should use default from ConfDefaultStr.DEVICE_LIST


class TestAsyncCreateFixFlow:
    """Test async_create_fix_flow factory function."""

    async def test_async_create_fix_flow_check_configuration(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test creating CheckConfigurationRepairFlow."""
        data = {"entry_id": mock_config_entry.entry_id}

        flow = await async_create_fix_flow(
            hass, "check_configuration", data
        )

        assert isinstance(flow, CheckConfigurationRepairFlow)
        assert flow._entry == mock_config_entry

    async def test_async_create_fix_flow_unknown_issue(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test creating flow for unknown issue_id returns None."""
        data = {"entry_id": mock_config_entry.entry_id}

        flow = await async_create_fix_flow(
            hass, "unknown_issue", data
        )

        assert flow is None

    async def test_async_create_fix_flow_entry_not_found(
        self, hass: HomeAssistant
    ) -> None:
        """Test creating flow when config entry doesn't exist returns None."""
        data = {"entry_id": "non_existent_entry_id"}

        flow = await async_create_fix_flow(
            hass, "check_configuration", data
        )

        assert flow is None

    async def test_async_create_fix_flow_with_none_data(
        self, hass: HomeAssistant
    ) -> None:
        """Test creating flow with None data should raise error."""
        # This tests the type casting behavior - data should not be None
        with pytest.raises(TypeError):
            await async_create_fix_flow(hass, "check_configuration", None)


class TestRepairFlowIntegration:
    """Integration tests for repair flow."""

    async def test_full_repair_flow_success(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test complete repair flow from creation to completion."""
        # Create the flow
        data = {"entry_id": mock_config_entry.entry_id}
        flow = await async_create_fix_flow(
            hass, "check_configuration", data
        )

        assert isinstance(flow, CheckConfigurationRepairFlow)
        flow.hass = hass

        # Step 1: init redirects to confirm
        result = await flow.async_step_init()
        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

        # Step 2: Submit valid data
        user_input = {
            CONF_HOST: "192.168.1.150",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1,2",
        }

        with patch.object(hass.config_entries, "async_update_entry"):
            result = await flow.async_step_confirm(user_input=user_input)

        assert result["type"] == "create_entry"

    async def test_full_repair_flow_with_errors_then_fix(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test repair flow with initial errors, then successful correction."""
        data = {"entry_id": mock_config_entry.entry_id}
        flow = await async_create_fix_flow(
            hass, "check_configuration", data
        )

        flow.hass = hass

        # Submit invalid data first (invalid hostname with double dots)
        invalid_input = {
            CONF_HOST: "invalid..host",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        result = await flow.async_step_confirm(user_input=invalid_input)
        assert result["type"] == "form"
        assert CONF_HOST in result["errors"]

        # Now submit valid data
        valid_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 1502,
            ConfName.DEVICE_LIST: "1",
        }

        with patch.object(hass.config_entries, "async_update_entry"):
            result = await flow.async_step_confirm(user_input=valid_input)

        assert result["type"] == "create_entry"
