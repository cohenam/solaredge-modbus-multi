"""Tests for the SolarEdge Modbus Multi switch module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import SunSpecNotImpl
from custom_components.solaredge_modbus_multi.switch import (
    SolarEdgeExternalProduction,
    SolarEdgeGridControl,
    SolarEdgeNegativeSiteLimit,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock()
    coordinator.data = {}
    return coordinator


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {"name": "Test SolarEdge"}
    return entry


@pytest.fixture
def mock_inverter_platform():
    """Create a mock inverter platform with typical decoded data."""
    platform = MagicMock()
    platform.uid_base = "se_inv_1"
    platform.device_info = {
        "identifiers": {("solaredge_modbus_multi", "se_inv_1")},
        "name": "SolarEdge Inverter 1",
        "manufacturer": "SolarEdge",
        "model": "SE10K",
    }
    platform.online = True
    platform.advanced_power_control = True

    # Default decoded model with E_Lim_Ctl_Mode and AdvPwrCtrlEn
    platform.decoded_model = {
        "E_Lim_Ctl_Mode": 0x0000,  # All bits off by default
        "AdvPwrCtrlEn": 0x0,  # Advanced power control disabled
    }

    # Mock write_registers and async_update
    platform.write_registers = AsyncMock()

    return platform


@pytest.fixture
def mock_hub():
    """Create a mock hub."""
    hub = MagicMock()
    hub.option_site_limit_control = True
    hub.option_detect_extras = True
    return hub


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    async def test_setup_with_site_limit_control(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator, mock_hub
    ):
        """Test setup with site limit control enabled."""
        inverter = MagicMock()
        inverter.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        inverter.advanced_power_control = False
        mock_hub.inverters = [inverter]

        hass.data = {
            "solaredge_modbus_multi": {
                "test_entry_123": {
                    "hub": mock_hub,
                    "coordinator": mock_coordinator,
                }
            }
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add ExternalProduction and NegativeSiteLimit
        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2
        assert isinstance(entities[0], SolarEdgeExternalProduction)
        assert isinstance(entities[1], SolarEdgeNegativeSiteLimit)

    async def test_setup_with_advanced_power_control(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator, mock_hub
    ):
        """Test setup with advanced power control enabled."""
        inverter = MagicMock()
        inverter.decoded_model = {"E_Lim_Ctl_Mode": 0x0000, "AdvPwrCtrlEn": 0x0}
        inverter.advanced_power_control = True
        mock_hub.inverters = [inverter]

        hass.data = {
            "solaredge_modbus_multi": {
                "test_entry_123": {
                    "hub": mock_hub,
                    "coordinator": mock_coordinator,
                }
            }
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add ExternalProduction, NegativeSiteLimit, and GridControl
        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3
        assert isinstance(entities[0], SolarEdgeExternalProduction)
        assert isinstance(entities[1], SolarEdgeNegativeSiteLimit)
        assert isinstance(entities[2], SolarEdgeGridControl)

    async def test_setup_with_site_limit_control_disabled(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator, mock_hub
    ):
        """Test setup with site limit control disabled."""
        inverter = MagicMock()
        inverter.decoded_model = {"AdvPwrCtrlEn": 0x0}
        inverter.advanced_power_control = True
        mock_hub.option_site_limit_control = False
        mock_hub.inverters = [inverter]

        hass.data = {
            "solaredge_modbus_multi": {
                "test_entry_123": {
                    "hub": mock_hub,
                    "coordinator": mock_coordinator,
                }
            }
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should only add GridControl
        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], SolarEdgeGridControl)

    async def test_setup_no_entities(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator, mock_hub
    ):
        """Test setup with no entities to add."""
        inverter = MagicMock()
        inverter.advanced_power_control = False
        mock_hub.option_site_limit_control = False
        mock_hub.option_detect_extras = False
        mock_hub.inverters = [inverter]

        hass.data = {
            "solaredge_modbus_multi": {
                "test_entry_123": {
                    "hub": mock_hub,
                    "coordinator": mock_coordinator,
                }
            }
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should not call async_add_entities
        assert async_add_entities.call_count == 0


class TestSolarEdgeExternalProduction:
    """Tests for SolarEdgeExternalProduction switch."""

    def test_entity_properties(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test basic entity properties."""
        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.unique_id == "se_inv_1_external_production"
        assert switch.name == "External Production"
        assert switch.entity_category == EntityCategory.CONFIG
        assert switch.entity_registry_enabled_default is False
        assert switch.should_poll is False
        assert switch.device_info == mock_inverter_platform.device_info

    def test_available_when_online(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability when platform is online."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is True

    def test_unavailable_when_offline(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when platform is offline."""
        mock_inverter_platform.online = False
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_not_implemented(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode is not implemented."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": SunSpecNotImpl.UINT16}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_key_missing(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode key is missing."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_is_on_when_bit_10_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns True when bit 10 is set."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000010000000000  # Bit 10 set
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 1  # Returns 1 when bit is set

    def test_is_on_when_bit_10_clear(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns False when bit 10 is clear."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000000000000000  # Bit 10 clear
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 0  # Returns 0 when bit is clear

    def test_is_on_with_other_bits_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on correctly extracts bit 10 when other bits are set."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b1111111111111111  # All bits set
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 1  # Returns 1 when bit is set

        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b1111101111111111  # Bit 10 clear, others set
        }

        assert switch.is_on == 0  # Returns 0 when bit is clear

    async def test_async_turn_on(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning on the switch sets bit 10."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000000000000000  # All bits off
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_on()

        # Verify bit 10 is set (value = 1024 = 0x0400)
        expected_value = 0b0000010000000000
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 57344
        # Verify the payload was created with the correct value
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_on_preserves_other_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning on the switch preserves other bits."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000100000000001  # Bits 0 and 11 set
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_on()

        # Verify bit 10 is set and other bits are preserved
        expected_value = 0b0000110000000001
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_off(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning off the switch clears bit 10."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000010000000000  # Bit 10 set
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_off()

        # Verify bit 10 is cleared
        expected_value = 0b0000000000000000
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 57344
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_off_preserves_other_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning off the switch preserves other bits."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000110000000001  # Bits 0, 10, and 11 set
        }

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_off()

        # Verify bit 10 is cleared and other bits are preserved
        expected_value = 0b0000100000000001
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_update_called(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_update is called after turn_on and turn_off."""
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_on()
            mock_update.assert_called_once()

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_off()
            mock_update.assert_called_once()

    def test_handle_coordinator_update(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test coordinator update triggers state write."""
        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_write_ha_state") as mock_write:
            switch._handle_coordinator_update()
            mock_write.assert_called_once()


class TestSolarEdgeNegativeSiteLimit:
    """Tests for SolarEdgeNegativeSiteLimit switch."""

    def test_entity_properties(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test basic entity properties."""
        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.unique_id == "se_inv_1_negative_site_limit"
        assert switch.name == "Negative Site Limit"
        assert switch.entity_category == EntityCategory.CONFIG
        assert switch.should_poll is False
        assert switch.device_info == mock_inverter_platform.device_info

    def test_available_when_online(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability when platform is online."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is True

    def test_unavailable_when_offline(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when platform is offline."""
        mock_inverter_platform.online = False
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_not_implemented(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode is not implemented."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": SunSpecNotImpl.UINT16}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_key_missing(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode key is missing."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_is_on_when_bit_11_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns True when bit 11 is set."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000100000000000  # Bit 11 set
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 1  # Returns 1 when bit is set

    def test_is_on_when_bit_11_clear(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns False when bit 11 is clear."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000000000000000  # Bit 11 clear
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 0  # Returns 0 when bit is clear

    def test_is_on_with_other_bits_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on correctly extracts bit 11 when other bits are set."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b1111111111111111  # All bits set
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on == 1  # Returns 1 when bit is set

        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b1111011111111111  # Bit 11 clear, others set
        }

        assert switch.is_on == 0  # Returns 0 when bit is clear

    async def test_async_turn_on(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning on the switch sets bit 11."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000000000000000  # All bits off
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_on()

        # Verify bit 11 is set (value = 2048 = 0x0800)
        expected_value = 0b0000100000000000
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 57344
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_on_preserves_other_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning on the switch preserves other bits."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000010000000001  # Bits 0 and 10 set
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_on()

        # Verify bit 11 is set and other bits are preserved
        expected_value = 0b0000110000000001
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_off(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning off the switch clears bit 11."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000100000000000  # Bit 11 set
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_off()

        # Verify bit 11 is cleared
        expected_value = 0b0000000000000000
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 57344
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_turn_off_preserves_other_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning off the switch preserves other bits."""
        mock_inverter_platform.decoded_model = {
            "E_Lim_Ctl_Mode": 0b0000110000000001  # Bits 0, 10, and 11 set
        }

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_off()

        # Verify bit 11 is cleared and other bits are preserved
        expected_value = 0b0000010000000001
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            expected_value,
            data_type=ModbusClientMixin.DATATYPE.UINT16,
            word_order="little",
        )

    async def test_async_update_called(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_update is called after turn_on and turn_off."""
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}

        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_on()
            mock_update.assert_called_once()

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_off()
            mock_update.assert_called_once()

    def test_handle_coordinator_update(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test coordinator update triggers state write."""
        switch = SolarEdgeNegativeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_write_ha_state") as mock_write:
            switch._handle_coordinator_update()
            mock_write.assert_called_once()


class TestSolarEdgeGridControl:
    """Tests for SolarEdgeGridControl switch."""

    def test_entity_properties(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test basic entity properties."""
        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.unique_id == "se_inv_1_adv_pwr_ctrl"
        assert switch.name == "Advanced Power Control"
        assert switch.entity_category == EntityCategory.CONFIG
        assert switch.should_poll is False
        assert switch.device_info == mock_inverter_platform.device_info

    def test_available_when_conditions_met(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability when all conditions are met."""
        mock_inverter_platform.online = True
        mock_inverter_platform.advanced_power_control = True
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is True

    def test_unavailable_when_offline(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when platform is offline."""
        mock_inverter_platform.online = False
        mock_inverter_platform.advanced_power_control = True
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_advanced_power_control_disabled(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when advanced power control is disabled."""
        mock_inverter_platform.online = True
        mock_inverter_platform.advanced_power_control = False
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_unavailable_when_key_missing(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when AdvPwrCtrlEn key is missing."""
        mock_inverter_platform.online = True
        mock_inverter_platform.advanced_power_control = True
        mock_inverter_platform.decoded_model = {}
        mock_coordinator.last_update_success = True

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.available is False

    def test_is_on_when_enabled(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns True when AdvPwrCtrlEn is 0x1."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x1}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on is True

    def test_is_on_when_disabled(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on returns False when AdvPwrCtrlEn is 0x0."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.is_on is False

    def test_is_on_with_other_values(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test is_on with various values."""
        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        # Test with value 2
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x2}
        assert switch.is_on is False

        # Test with value 0xFF
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0xFF}
        assert switch.is_on is False

    async def test_async_turn_on(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning on the switch sets value to 0x1."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_on()

        # Verify value is set to 0x1
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 61762
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            0x1,
            data_type=ModbusClientMixin.DATATYPE.INT32,
            word_order="little",
        )

    async def test_async_turn_off(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test turning off the switch sets value to 0x0."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x1}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            await switch.async_turn_off()

        # Verify value is set to 0x0
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args

        assert call_args[1]["address"] == 61762
        payload = call_args[1]["payload"]
        assert payload == ModbusClientMixin.convert_to_registers(
            0x0,
            data_type=ModbusClientMixin.DATATYPE.INT32,
            word_order="little",
        )

    async def test_async_update_called(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_update is called after turn_on and turn_off."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_on()
            mock_update.assert_called_once()

        with patch.object(
            switch, "async_update", new_callable=AsyncMock
        ) as mock_update:
            await switch.async_turn_off()
            mock_update.assert_called_once()

    def test_handle_coordinator_update(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test coordinator update triggers state write."""
        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_write_ha_state") as mock_write:
            switch._handle_coordinator_update()
            mock_write.assert_called_once()

    async def test_direct_value_write_not_bit_manipulation(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test GridControl writes direct values, not bit manipulation."""
        mock_inverter_platform.decoded_model = {"AdvPwrCtrlEn": 0x0}

        switch = SolarEdgeGridControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(switch, "async_update", new_callable=AsyncMock):
            # Turn on writes 0x1
            await switch.async_turn_on()

            call_args_on = mock_inverter_platform.write_registers.call_args
            payload_on = call_args_on[1]["payload"]

            # Reset mock
            mock_inverter_platform.write_registers.reset_mock()

            # Turn off writes 0x0
            await switch.async_turn_off()

            call_args_off = mock_inverter_platform.write_registers.call_args
            payload_off = call_args_off[1]["payload"]

            # Verify direct values (INT32, not UINT16 like bit manipulation)
            assert payload_on == ModbusClientMixin.convert_to_registers(
                0x1, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
            )
            assert payload_off == ModbusClientMixin.convert_to_registers(
                0x0, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
            )


class TestSwitchBase:
    """Tests for SolarEdgeSwitchBase common functionality."""

    def test_config_entry_properties(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test config entry related properties."""
        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        assert switch.config_entry_id == "test_entry_123"
        assert switch.config_entry_name == "Test SolarEdge"

    def test_coordinator_integration(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test integration with coordinator."""
        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        # Verify coordinator is set
        assert switch.coordinator == mock_coordinator

    def test_available_depends_on_coordinator(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability depends on both coordinator and platform."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model = {"E_Lim_Ctl_Mode": 0x0000}

        switch = SolarEdgeExternalProduction(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        # Both coordinator and platform must be available
        mock_coordinator.last_update_success = True
        assert switch.available is True

        mock_coordinator.last_update_success = False
        assert switch.available is False
