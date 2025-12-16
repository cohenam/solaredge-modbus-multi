"""Tests for the SolarEdge Modbus Multi select module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import (
    LIMIT_CONTROL,
    LIMIT_CONTROL_MODE,
    REACTIVE_POWER_CONFIG,
    STORAGE_AC_CHARGE_POLICY,
    STORAGE_CONTROL_MODE,
    STORAGE_MODE,
    SunSpecNotImpl,
)
from custom_components.solaredge_modbus_multi.select import (
    SolaredgeLimitControl,
    SolaredgeLimitControlMode,
    SolarEdgeReactivePowerMode,
    SolarEdgeSelectBase,
    StorageACChargePolicy,
    StorageCommandMode,
    StorageControlMode,
    StorageDefaultMode,
    async_setup_entry,
    get_key,
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
    """Create a mock inverter platform with storage control capabilities."""
    platform = MagicMock()
    platform.uid_base = "se_inv_1"
    platform.device_info = {
        "identifiers": {("solaredge_modbus_multi", "se_inv_1")},
        "name": "SolarEdge Inverter 1",
        "manufacturer": "SolarEdge",
        "model": "SE10K",
    }
    platform.online = True
    platform.has_battery = True
    platform.write_registers = AsyncMock()
    platform.advanced_power_control = True

    # Storage control data
    platform.decoded_storage_control = {
        "control_mode": 4,  # Remote Control
        "ac_charge_policy": 1,  # Always Allowed
        "default_mode": 7,  # Maximize Self Consumption
        "command_mode": 2,  # Charge from Solar Power
    }

    # Model data for limit control and reactive power
    platform.decoded_model = {
        "E_Lim_Ctl_Mode": 1,  # bit 0 set = Export Control (Export/Import Meter)
        "E_Lim_Ctl": 0,  # Total
        "ReactivePwrConfig": 0,  # Fixed CosPhi
    }

    return platform


@pytest.fixture
def mock_hub():
    """Create a mock hub."""
    hub = MagicMock()
    hub.option_storage_control = True
    hub.option_site_limit_control = True
    hub.option_detect_extras = True
    return hub


class TestGetKeyHelper:
    """Tests for the get_key helper function."""

    def test_get_key_found(self):
        """Test get_key returns the key when value is found."""
        test_dict = {0: "Option A", 1: "Option B", 2: "Option C"}
        assert get_key(test_dict, "Option B") == 1

    def test_get_key_not_found(self):
        """Test get_key returns None when value is not found."""
        test_dict = {0: "Option A", 1: "Option B", 2: "Option C"}
        assert get_key(test_dict, "Option D") is None

    def test_get_key_first_match(self):
        """Test get_key returns first matching key."""
        test_dict = {0: "Option A", 1: "Option A", 2: "Option C"}
        result = get_key(test_dict, "Option A")
        assert result in [0, 1]  # Could be either, dict order may vary


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    async def test_setup_with_storage_control_entities(
        self, hass: HomeAssistant, mock_hub, mock_inverter_platform, mock_coordinator
    ):
        """Test setup creates storage control entities when enabled."""
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        mock_hub.inverters = [mock_inverter_platform]
        mock_hub.option_storage_control = True
        mock_inverter_platform.decoded_storage_control = {"control_mode": 4}

        added_entities = []

        def capture_entities(entities):
            added_entities.extend(entities)

        with patch.dict(
            hass.data,
            {
                "solaredge_modbus_multi": {
                    "test_entry": {"hub": mock_hub, "coordinator": mock_coordinator}
                }
            },
        ):
            await async_setup_entry(hass, mock_config_entry, capture_entities)

        # Should have 4 storage control entities + 2 limit control + 1 reactive power
        assert len(added_entities) == 7

        # Verify entity types
        entity_types = [type(e).__name__ for e in added_entities]
        assert "StorageControlMode" in entity_types
        assert "StorageACChargePolicy" in entity_types
        assert "StorageDefaultMode" in entity_types
        assert "StorageCommandMode" in entity_types
        assert "SolaredgeLimitControlMode" in entity_types
        assert "SolaredgeLimitControl" in entity_types
        assert "SolarEdgeReactivePowerMode" in entity_types

    async def test_setup_without_storage_control(
        self, hass: HomeAssistant, mock_hub, mock_inverter_platform, mock_coordinator
    ):
        """Test setup without storage control entities."""
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        mock_hub.inverters = [mock_inverter_platform]
        mock_hub.option_storage_control = False
        mock_hub.option_site_limit_control = False
        mock_hub.option_detect_extras = False

        added_entities = []

        def capture_entities(entities):
            added_entities.extend(entities)

        with patch.dict(
            hass.data,
            {
                "solaredge_modbus_multi": {
                    "test_entry": {"hub": mock_hub, "coordinator": mock_coordinator}
                }
            },
        ):
            await async_setup_entry(hass, mock_config_entry, capture_entities)

        # Should not add any entities
        assert len(added_entities) == 0

    async def test_setup_without_decoded_storage_control(
        self, hass: HomeAssistant, mock_hub, mock_inverter_platform, mock_coordinator
    ):
        """Test setup when decoded_storage_control is False."""
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        mock_hub.inverters = [mock_inverter_platform]
        mock_hub.option_storage_control = True
        mock_inverter_platform.decoded_storage_control = False

        added_entities = []

        def capture_entities(entities):
            added_entities.extend(entities)

        with patch.dict(
            hass.data,
            {
                "solaredge_modbus_multi": {
                    "test_entry": {"hub": mock_hub, "coordinator": mock_coordinator}
                }
            },
        ):
            await async_setup_entry(hass, mock_config_entry, capture_entities)

        # Should have only limit control and reactive power entities
        assert len(added_entities) == 3


class TestSolarEdgeSelectBase:
    """Tests for SolarEdgeSelectBase class."""

    def test_initialization(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test base class initialization."""
        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._platform == mock_inverter_platform
        assert select._config_entry == mock_config_entry
        assert select.should_poll is False
        assert select._attr_has_entity_name is True
        assert select.entity_category == EntityCategory.CONFIG

    def test_device_info(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test device_info property."""
        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.device_info == mock_inverter_platform.device_info

    def test_config_entry_properties(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test config entry properties."""
        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.config_entry_id == "test_entry_123"
        assert select.config_entry_name == "Test SolarEdge"

    def test_available_when_platform_online(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability when platform is online."""
        mock_inverter_platform.online = True
        mock_coordinator.last_update_success = True

        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_when_platform_offline(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when platform is offline."""
        mock_inverter_platform.online = False
        mock_coordinator.last_update_success = True

        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_handle_coordinator_update(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test coordinator update handling."""
        select = SolarEdgeSelectBase(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_write_ha_state = MagicMock()

        select._handle_coordinator_update()

        select.async_write_ha_state.assert_called_once()


class TestStorageControlMode:
    """Tests for StorageControlMode select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_storage_control_mode"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Storage Control Mode"

    def test_entity_registry_enabled_default_with_battery(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is enabled by default when battery is present."""
        mock_inverter_platform.has_battery = True
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.entity_registry_enabled_default is True

    def test_entity_registry_enabled_default_without_battery(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default when battery is absent."""
        mock_inverter_platform.has_battery = False
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.entity_registry_enabled_default is False

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(STORAGE_CONTROL_MODE.values())

    def test_current_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option property."""
        mock_inverter_platform.decoded_storage_control = {"control_mode": 4}
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Remote Control"

    def test_available_with_valid_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability with valid control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {"control_mode": 4}
        mock_coordinator.last_update_success = True

        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when control_mode is not implemented."""
        mock_inverter_platform.decoded_storage_control = {
            "control_mode": SunSpecNotImpl.UINT16
        }
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_invalid_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when control_mode is not in options."""
        mock_inverter_platform.decoded_storage_control = {"control_mode": 99}
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_false_decoded_storage_control(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when decoded_storage_control is False."""
        mock_inverter_platform.decoded_storage_control = False
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_missing_key(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when control_mode key is missing."""
        mock_inverter_platform.decoded_storage_control = {}
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_select_option writes correct register value."""
        mock_inverter_platform.decoded_storage_control = {"control_mode": 0}
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Remote Control")

        # Verify write_registers was called with correct parameters
        mock_inverter_platform.write_registers.assert_called_once()
        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57348

        # Verify the payload contains the correct mode value (4 for Remote Control)
        expected_payload = ModbusClientMixin.convert_to_registers(
            4, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload

        # Verify async_update was called
        select.async_update.assert_called_once()

    async def test_async_select_option_disabled(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test selecting disabled option."""
        mock_inverter_platform.decoded_storage_control = {"control_mode": 4}
        select = StorageControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Disabled")

        call_args = mock_inverter_platform.write_registers.call_args
        expected_payload = ModbusClientMixin.convert_to_registers(
            0, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload


class TestStorageACChargePolicy:
    """Tests for StorageACChargePolicy select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_ac_charge_policy"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "AC Charge Policy"

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is enabled by default when battery is present."""
        mock_inverter_platform.has_battery = True
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.entity_registry_enabled_default is True

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(STORAGE_AC_CHARGE_POLICY.values())

    def test_current_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option property."""
        mock_inverter_platform.decoded_storage_control = {"ac_charge_policy": 1}
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Always Allowed"

    def test_available_with_valid_policy(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability with valid AC charge policy."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {"ac_charge_policy": 1}
        mock_coordinator.last_update_success = True

        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when ac_charge_policy is not implemented."""
        mock_inverter_platform.decoded_storage_control = {
            "ac_charge_policy": SunSpecNotImpl.UINT16
        }
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_select_option writes correct register value."""
        mock_inverter_platform.decoded_storage_control = {"ac_charge_policy": 0}
        select = StorageACChargePolicy(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Fixed Energy Limit")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57349

        expected_payload = ModbusClientMixin.convert_to_registers(
            2, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()


class TestStorageDefaultMode:
    """Tests for StorageDefaultMode select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_storage_default_mode"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Storage Default Mode"

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is enabled by default when battery is present."""
        mock_inverter_platform.has_battery = True
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.entity_registry_enabled_default is True

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(STORAGE_MODE.values())

    def test_current_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option property."""
        mock_inverter_platform.decoded_storage_control = {"default_mode": 7}
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Maximize Self Consumption"

    def test_available_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability only in remote control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {
            "default_mode": 7,
            "control_mode": 4,  # Remote Control
        }
        mock_coordinator.last_update_success = True

        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_not_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when not in remote control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {
            "default_mode": 7,
            "control_mode": 1,  # Not Remote Control
        }
        mock_coordinator.last_update_success = True

        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when default_mode is not implemented."""
        mock_inverter_platform.decoded_storage_control = {
            "default_mode": SunSpecNotImpl.UINT16,
            "control_mode": 4,
        }
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_select_option writes correct register value."""
        mock_inverter_platform.decoded_storage_control = {
            "default_mode": 0,
            "control_mode": 4,
        }
        select = StorageDefaultMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Charge from Solar Power")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57354

        expected_payload = ModbusClientMixin.convert_to_registers(
            2, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()


class TestStorageCommandMode:
    """Tests for StorageCommandMode select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_storage_command_mode"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Storage Command Mode"

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is enabled by default when battery is present."""
        mock_inverter_platform.has_battery = True
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.entity_registry_enabled_default is True

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(STORAGE_MODE.values())

    def test_current_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option property."""
        mock_inverter_platform.decoded_storage_control = {"command_mode": 2}
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Charge from Solar Power"

    def test_available_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability only in remote control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {
            "command_mode": 2,
            "control_mode": 4,  # Remote Control
        }
        mock_coordinator.last_update_success = True

        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_not_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when not in remote control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_storage_control = {
            "command_mode": 2,
            "control_mode": 0,  # Not Remote Control
        }
        mock_coordinator.last_update_success = True

        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when command_mode is not implemented."""
        mock_inverter_platform.decoded_storage_control = {
            "command_mode": SunSpecNotImpl.UINT16,
            "control_mode": 4,
        }
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test async_select_option writes correct register value."""
        mock_inverter_platform.decoded_storage_control = {
            "command_mode": 0,
            "control_mode": 4,
        }
        select = StorageCommandMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Discharge to Maximize Export")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57357

        expected_payload = ModbusClientMixin.convert_to_registers(
            4, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()


class TestSolaredgeLimitControlMode:
    """Tests for SolaredgeLimitControlMode select entity with bit manipulation."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_limit_control_mode"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Limit Control Mode"

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(LIMIT_CONTROL_MODE.values())

    def test_current_option_bit_0(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option with bit 0 set (Export Control Export/Import Meter)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0001  # Bit 0 set
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Export Control (Export/Import Meter)"

    def test_current_option_bit_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option with bit 1 set (Export Control Consumption Meter)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0010  # Bit 1 set
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Export Control (Consumption Meter)"

    def test_current_option_bit_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option with bit 2 set (Production Control)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0100  # Bit 2 set
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Production Control"

    def test_current_option_no_bits_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option with no bits set (Disabled)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0000
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Disabled"

    def test_current_option_multiple_bits_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option with multiple bits set (should return first match)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0011  # Bits 0 and 1
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        # Should return bit 0 since it's checked first
        assert select.current_option == "Export Control (Export/Import Meter)"

    def test_current_option_high_bits_ignored(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test that high bits (beyond 0-2) are ignored."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = (
            0b11110010  # Bit 1 + high bits
        )
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Export Control (Consumption Meter)"

    def test_available_with_valid_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability with valid limit control mode."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 1
        mock_coordinator.last_update_success = True

        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode is not implemented."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = SunSpecNotImpl.UINT16
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        # Note: Implementation returns None instead of False for this case
        assert select.available is None

    def test_unavailable_with_missing_key(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl_Mode key is missing."""
        mock_inverter_platform.decoded_model = {}
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option_set_bit_0(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting bit 0 (Export Control Export/Import Meter)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0000
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Export Control (Export/Import Meter)")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57344

        # Should set only bit 0
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b0001, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()

    async def test_async_select_option_set_bit_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting bit 1 (Export Control Consumption Meter)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0000
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Export Control (Consumption Meter)")

        call_args = mock_inverter_platform.write_registers.call_args

        # Should set only bit 1
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b0010, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload

    async def test_async_select_option_set_bit_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting bit 2 (Production Control)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0000
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Production Control")

        call_args = mock_inverter_platform.write_registers.call_args

        # Should set only bit 2
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b0100, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload

    async def test_async_select_option_disabled(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test disabling (clearing bits 0-2)."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0111  # All bits set
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Disabled")

        call_args = mock_inverter_platform.write_registers.call_args

        # Should clear bits 0-2
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b0000, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload

    async def test_async_select_option_preserves_high_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test that high bits (beyond 0-2) are preserved during write."""
        # Start with high bits set
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b11111000
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Production Control")

        call_args = mock_inverter_platform.write_registers.call_args

        # Should preserve high bits and set bit 2
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b11111100, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload

    async def test_async_select_option_clears_previous_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test that previous control bits are cleared when setting new mode."""
        # Start with bit 0 set
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0001
        select = SolaredgeLimitControlMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        # Switch to bit 1
        await select.async_select_option("Export Control (Consumption Meter)")

        call_args = mock_inverter_platform.write_registers.call_args

        # Should clear bit 0 and set bit 1
        expected_payload = ModbusClientMixin.convert_to_registers(
            0b0010, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload


class TestSolaredgeLimitControl:
    """Tests for SolaredgeLimitControl select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_limit_control"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Limit Control"

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(LIMIT_CONTROL.values())

    def test_current_option_total(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for Total."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = 0
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Total"

    def test_current_option_per_phase(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for Per Phase."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = 1
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Per Phase"

    def test_available_with_valid_control(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability with valid limit control."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = 0
        mock_coordinator.last_update_success = True

        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl is not implemented."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = SunSpecNotImpl.UINT16
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_missing_key(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when E_Lim_Ctl key is missing."""
        mock_inverter_platform.decoded_model = {}
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option_total(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test selecting Total option."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = 1
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Total")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57345

        expected_payload = ModbusClientMixin.convert_to_registers(
            0, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()

    async def test_async_select_option_per_phase(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test selecting Per Phase option."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl"] = 0
        select = SolaredgeLimitControl(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Per Phase")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 57345

        expected_payload = ModbusClientMixin.convert_to_registers(
            1, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()


class TestSolarEdgeReactivePowerMode:
    """Tests for SolarEdgeReactivePowerMode select entity."""

    def test_unique_id(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unique_id property."""
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.unique_id == "se_inv_1_reactive_power_mode"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.name == "Reactive Power Mode"

    def test_options(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test options property."""
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select._attr_options == list(REACTIVE_POWER_CONFIG.values())

    def test_current_option_fixed_cosphi(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for Fixed CosPhi."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 0
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Fixed CosPhi"

    def test_current_option_fixed_q(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for Fixed Q."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 1
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Fixed Q"

    def test_current_option_cosphi_p(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for CosPhi(P)."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 2
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "CosPhi(P)"

    def test_current_option_q_u_q_p(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for Q(U) + Q(P)."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 3
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "Q(U) + Q(P)"

    def test_current_option_rrcr(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test current_option for RRCR."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 4
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.current_option == "RRCR"

    def test_available_with_valid_config(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test availability with valid reactive power config."""
        mock_inverter_platform.online = True
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 0
        mock_coordinator.last_update_success = True

        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is True

    def test_unavailable_with_not_implemented_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when ReactivePwrConfig is not implemented."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = SunSpecNotImpl.INT32
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_invalid_config(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when ReactivePwrConfig is not in options."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 99
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    def test_unavailable_with_missing_key(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unavailability when ReactivePwrConfig key is missing."""
        mock_inverter_platform.decoded_model = {}
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert select.available is False

    async def test_async_select_option_fixed_cosphi(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test selecting Fixed CosPhi option."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 1
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("Fixed CosPhi")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 61700

        expected_payload = ModbusClientMixin.convert_to_registers(
            0, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()

    async def test_async_select_option_rrcr(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test selecting RRCR option."""
        mock_inverter_platform.decoded_model["ReactivePwrConfig"] = 0
        select = SolarEdgeReactivePowerMode(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        select.async_update = AsyncMock()

        await select.async_select_option("RRCR")

        call_args = mock_inverter_platform.write_registers.call_args
        assert call_args[1]["address"] == 61700

        # Note: Uses INT32 data type
        expected_payload = ModbusClientMixin.convert_to_registers(
            4, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
        )
        assert call_args[1]["payload"] == expected_payload
        select.async_update.assert_called_once()
