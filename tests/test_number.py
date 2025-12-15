"""Tests for the SolarEdge Modbus Multi number module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import (
    DOMAIN,
    BatteryLimit,
    SunSpecNotImpl,
)
from custom_components.solaredge_modbus_multi.number import (
    SolarEdgeActivePowerLimitSet,
    SolarEdgeCosPhiSet,
    SolarEdgeCurrentLimit,
    SolarEdgeExternalProductionMax,
    SolarEdgeNumberBase,
    SolarEdgePowerReduce,
    SolarEdgeSiteLimit,
    StorageACChargeLimit,
    StorageBackupReserve,
    StorageChargeLimit,
    StorageCommandTimeout,
    StorageDischargeLimit,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
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
    platform.has_battery = True
    platform.global_power_control = True
    platform.advanced_power_control = True

    # Common model data for inverter
    platform.decoded_model = {
        "I_Power_Limit": 80,  # 80%
        "I_CosPhi": 0.95,
        "PowerReduce": 100.0,
        "MaxCurrent": 32.0,
        "E_Site_Limit": 5000,
        "Ext_Prod_Max": 10000,
        "E_Lim_Ctl_Mode": 0b10000000111,  # Bits 0,1,2,10 set
    }

    # Storage control data
    platform.decoded_storage_control = {
        "control_mode": 4,  # Remote control
        "ac_charge_policy": 2,  # Fixed Energy Limit
        "ac_charge_limit": 10.0,  # 10 kWh
        "backup_reserve": 30.0,  # 30%
        "command_timeout": 600,  # 600 seconds
        "charge_limit": 5000.0,  # 5000W
        "discharge_limit": 4000.0,  # 4000W
    }

    platform.write_registers = AsyncMock()

    return platform


@pytest.fixture
def mock_inverter_platform_no_battery():
    """Create a mock inverter platform without battery."""
    platform = MagicMock()
    platform.uid_base = "se_inv_1"
    platform.device_info = {
        "identifiers": {("solaredge_modbus_multi", "se_inv_1")},
        "name": "SolarEdge Inverter 1",
    }
    platform.online = True
    platform.has_battery = False
    platform.decoded_storage_control = False
    platform.decoded_model = {}
    return platform


# StorageACChargeLimit Tests


class TestStorageACChargeLimit:
    """Tests for StorageACChargeLimit number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_storage_ac_charge_limit"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "AC Charge Limit"

    def test_entity_registry_enabled_default_with_battery(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is enabled by default when battery is present."""
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is True

    def test_entity_registry_enabled_default_without_battery(
        self, mock_inverter_platform_no_battery, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default when no battery."""
        entity = StorageACChargeLimit(
            mock_inverter_platform_no_battery, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is False

    def test_native_unit_of_measurement_policy_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unit is kWh for AC charge policy 2 (Fixed Energy Limit)."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 2
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_native_unit_of_measurement_policy_3(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test unit is % for AC charge policy 3 (Percent of Production)."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 3
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == PERCENTAGE

    def test_native_max_value_policy_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test max value is 100000000 for AC charge policy 2."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 2
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_max_value == 100000000

    def test_native_max_value_policy_3(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test max value is 100 for AC charge policy 3."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 3
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_max_value == 100

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 10

    def test_available_policy_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available for AC charge policy 2."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 2
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_available_policy_3(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available for AC charge policy 3."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 3
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_policy_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available for AC charge policy 1."""
        mock_inverter_platform.decoded_storage_control["ac_charge_policy"] = 1
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_no_storage_control(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when storage control is False."""
        mock_inverter_platform.decoded_storage_control = False
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_storage_control["ac_charge_limit"] = -1
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x4220, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(40.0)

            mock_convert.assert_called_once_with(
                40.0,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57350, payload=[0x4220, 0x0000]
            )


# StorageBackupReserve Tests


class TestStorageBackupReserve:
    """Tests for StorageBackupReserve number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_storage_backup_reserve"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Backup Reserve"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is percentage."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == PERCENTAGE

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 100

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 30

    def test_available(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available with valid data."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_value_over_100(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 100."""
        mock_inverter_platform.decoded_storage_control["backup_reserve"] = 101
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_storage_control["backup_reserve"] = -1
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x4248, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(50)

            mock_convert.assert_called_once_with(
                50,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57352, payload=[0x4248, 0x0000]
            )


# StorageCommandTimeout Tests


class TestStorageCommandTimeout:
    """Tests for StorageCommandTimeout number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_storage_command_timeout"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Storage Command Timeout"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is seconds."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfTime.SECONDS

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 86400  # 24 hours

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 600

    def test_available_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available in remote control mode (4)."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 4
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_not_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when not in remote control mode."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 1
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_value_over_max(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 86400."""
        mock_inverter_platform.decoded_storage_control["command_timeout"] = 90000
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = StorageCommandTimeout(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x0E10, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(3600)

            mock_convert.assert_called_once_with(
                3600,
                data_type=ModbusClientMixin.DATATYPE.UINT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57355, payload=[0x0E10, 0x0000]
            )


# StorageChargeLimit Tests


class TestStorageChargeLimit:
    """Tests for StorageChargeLimit number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_storage_charge_limit"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Storage Charge Limit"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is watts."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfPower.WATT

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == BatteryLimit.ChargeMax

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 5000

    def test_available_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available in remote control mode."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 4
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_not_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when not in remote control mode."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 2
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_storage_control["charge_limit"] = -1
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = StorageChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x459C, 0x4000]
        ) as mock_convert:
            await entity.async_set_native_value(5000)

            mock_convert.assert_called_once_with(
                5000,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57358, payload=[0x459C, 0x4000]
            )


# StorageDischargeLimit Tests


class TestStorageDischargeLimit:
    """Tests for StorageDischargeLimit number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_storage_discharge_limit"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Storage Discharge Limit"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is watts."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfPower.WATT

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == BatteryLimit.DischargeMax

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 4000

    def test_available_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available in remote control mode."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 4
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_not_in_remote_control_mode(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when not in remote control mode."""
        mock_inverter_platform.decoded_storage_control["control_mode"] = 3
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_storage_control["discharge_limit"] = -1
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = StorageDischargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x457A, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(4000)

            mock_convert.assert_called_once_with(
                4000,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57360, payload=[0x457A, 0x0000]
            )


# SolarEdgeSiteLimit Tests


class TestSolarEdgeSiteLimit:
    """Tests for SolarEdgeSiteLimit number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_site_limit"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Site Limit"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is watts."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfPower.WATT

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 1000000

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 5000

    def test_native_value_negative_returns_zero(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value returns 0 when value is negative."""
        mock_inverter_platform.decoded_model["E_Site_Limit"] = -100
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 0

    def test_available_with_control_mode_bit_0(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available when E_Lim_Ctl_Mode bit 0 is set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b001
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available

    def test_available_with_control_mode_bit_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available when E_Lim_Ctl_Mode bit 1 is set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b010
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available

    def test_available_with_control_mode_bit_2(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available when E_Lim_Ctl_Mode bit 2 is set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b100
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available

    def test_not_available_without_control_mode_bits(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when E_Lim_Ctl_Mode bits 0,1,2 are not set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b0000
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert not entity.available

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x45FA, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(8000)

            mock_convert.assert_called_once_with(
                8000,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57346, payload=[0x45FA, 0x0000]
            )


# SolarEdgeExternalProductionMax Tests


class TestSolarEdgeExternalProductionMax:
    """Tests for SolarEdgeExternalProductionMax number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_external_production_max"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "External Production Max"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is watts."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfPower.WATT

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 1000000

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 10000

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is False

    def test_available_with_bit_10_set(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available when E_Lim_Ctl_Mode bit 10 is set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b10000000000  # Bit 10
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available

    def test_not_available_without_bit_10(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when E_Lim_Ctl_Mode bit 10 is not set."""
        mock_inverter_platform.decoded_model["E_Lim_Ctl_Mode"] = 0b00000000111
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert not entity.available

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_model["Ext_Prod_Max"] = -1
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgeExternalProductionMax(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x461C, 0x4000]
        ) as mock_convert:
            await entity.async_set_native_value(10000)

            mock_convert.assert_called_once_with(
                10000,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=57362, payload=[0x461C, 0x4000]
            )


# SolarEdgeActivePowerLimitSet Tests


class TestSolarEdgeActivePowerLimitSet:
    """Tests for SolarEdgeActivePowerLimitSet number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_active_power_limit_set"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Active Power Limit"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is percentage."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == PERCENTAGE

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 100

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 80

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity enabled default based on global_power_control."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is True

        mock_inverter_platform.global_power_control = False
        assert entity.entity_registry_enabled_default is False

    def test_available(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available with valid data."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_value_over_100(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 100."""
        mock_inverter_platform.decoded_model["I_Power_Limit"] = 101
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_model["I_Power_Limit"] = -1
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x0050]
        ) as mock_convert:
            await entity.async_set_native_value(80)

            mock_convert.assert_called_once_with(
                80,
                data_type=ModbusClientMixin.DATATYPE.UINT16,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=61441, payload=[0x0050]
            )


# SolarEdgeCosPhiSet Tests


class TestSolarEdgeCosPhiSet:
    """Tests for SolarEdgeCosPhiSet number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_cosphi_set"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "CosPhi"

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == -1.0
        assert entity.native_max_value == 1.0

    def test_native_step(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native step value."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_step == 0.1

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 0.9  # rounded from 0.95

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is False

    def test_available(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available with valid data."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_value_over_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 1.0."""
        mock_inverter_platform.decoded_model["I_CosPhi"] = 1.1
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_value_under_minus_1(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value < -1.0."""
        mock_inverter_platform.decoded_model["I_CosPhi"] = -1.1
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgeCosPhiSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x3F73, 0x3333]
        ) as mock_convert:
            await entity.async_set_native_value(0.95)

            mock_convert.assert_called_once_with(
                0.95,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=61442, payload=[0x3F73, 0x3333]
            )


# SolarEdgePowerReduce Tests


class TestSolarEdgePowerReduce:
    """Tests for SolarEdgePowerReduce number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_power_reduce"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Power Reduce"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is percentage."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == PERCENTAGE

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 100

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 100

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is False

    def test_available(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available with valid data."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_value_over_100(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 100."""
        mock_inverter_platform.decoded_model["PowerReduce"] = 101
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_model["PowerReduce"] = -1
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgePowerReduce(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x4248, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(50.0)

            mock_convert.assert_called_once_with(
                50.0,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=61760, payload=[0x4248, 0x0000]
            )


# SolarEdgeCurrentLimit Tests


class TestSolarEdgeCurrentLimit:
    """Tests for SolarEdgeCurrentLimit number entity."""

    def test_unique_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test unique_id property."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.unique_id == "se_inv_1_max_current"

    def test_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test name property."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.name == "Current Limit"

    def test_native_unit_of_measurement(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_unit_of_measurement is ampere."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_unit_of_measurement == UnitOfElectricCurrent.AMPERE

    def test_native_min_max_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native min and max values."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_min_value == 0
        assert entity.native_max_value == 256

    def test_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test native_value property."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.native_value == 32

    def test_entity_registry_enabled_default(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is disabled by default."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.entity_registry_enabled_default is False

    def test_available(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is available with valid data."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is True

    def test_not_available_value_over_256(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value > 256."""
        mock_inverter_platform.decoded_model["MaxCurrent"] = 257
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_not_available_negative_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when value is negative."""
        mock_inverter_platform.decoded_model["MaxCurrent"] = -1
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test setting native value writes to correct register."""
        entity = SolarEdgeCurrentLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )

        with patch.object(
            ModbusClientMixin, "convert_to_registers", return_value=[0x4200, 0x0000]
        ) as mock_convert:
            await entity.async_set_native_value(32.0)

            mock_convert.assert_called_once_with(
                32.0,
                data_type=ModbusClientMixin.DATATYPE.FLOAT32,
                word_order="little",
            )
            mock_inverter_platform.write_registers.assert_called_once_with(
                address=61838, payload=[0x4200, 0x0000]
            )


# async_setup_entry Tests


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_with_storage_control(self):
        """Test setup with storage control enabled."""
        hass = MagicMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        async_add_entities = MagicMock()

        # Create mock hub
        hub = MagicMock()
        hub.option_detect_extras = True
        hub.option_storage_control = True
        hub.option_site_limit_control = False

        # Create mock inverter
        inverter = MagicMock()
        inverter.decoded_storage_control = True
        inverter.has_battery = True
        inverter.advanced_power_control = True
        hub.inverters = [inverter]

        coordinator = MagicMock()

        hass.data = {DOMAIN: {config_entry.entry_id: {"hub": hub, "coordinator": coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Verify entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) > 0

    @pytest.mark.asyncio
    async def test_setup_with_site_limit_control(self):
        """Test setup with site limit control enabled."""
        hass = MagicMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        async_add_entities = MagicMock()

        # Create mock hub
        hub = MagicMock()
        hub.option_detect_extras = False
        hub.option_storage_control = False
        hub.option_site_limit_control = True

        # Create mock inverter
        inverter = MagicMock()
        inverter.decoded_storage_control = False
        inverter.has_battery = False
        inverter.advanced_power_control = False
        hub.inverters = [inverter]

        coordinator = MagicMock()

        hass.data = {DOMAIN: {config_entry.entry_id: {"hub": hub, "coordinator": coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Verify entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2  # SiteLimit and ExternalProductionMax

    @pytest.mark.asyncio
    async def test_setup_with_no_options_enabled(self):
        """Test setup with no options enabled."""
        hass = MagicMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        async_add_entities = MagicMock()

        # Create mock hub
        hub = MagicMock()
        hub.option_detect_extras = False
        hub.option_storage_control = False
        hub.option_site_limit_control = False

        hub.inverters = []

        coordinator = MagicMock()

        hass.data = {DOMAIN: {config_entry.entry_id: {"hub": hub, "coordinator": coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Verify no entities were added
        assert not async_add_entities.called


# SolarEdgeNumberBase Tests


class TestSolarEdgeNumberBase:
    """Tests for SolarEdgeNumberBase class."""

    def test_device_info(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test device_info property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.device_info == mock_inverter_platform.device_info

    def test_config_entry_id(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test config_entry_id property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.config_entry_id == "test_entry_123"

    def test_config_entry_name(self, mock_inverter_platform, mock_config_entry, mock_coordinator):
        """Test config_entry_name property."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.config_entry_name == "Test SolarEdge"

    def test_available_platform_offline(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test entity is not available when platform is offline."""
        mock_inverter_platform.online = False
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_handle_coordinator_update(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test coordinator update handler."""
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        with patch.object(entity, "async_write_ha_state") as mock_write:
            entity._handle_coordinator_update()
            mock_write.assert_called_once()


# Edge Cases and Error Handling


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_storage_ac_charge_limit_key_error(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test StorageACChargeLimit handles KeyError gracefully."""
        mock_inverter_platform.decoded_storage_control = {}
        entity = StorageACChargeLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_storage_backup_reserve_type_error(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test StorageBackupReserve handles TypeError gracefully."""
        mock_inverter_platform.decoded_storage_control = None
        entity = StorageBackupReserve(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_site_limit_key_error(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test SolarEdgeSiteLimit handles KeyError gracefully."""
        mock_inverter_platform.decoded_model = {}
        entity = SolarEdgeSiteLimit(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False

    def test_active_power_limit_sunspec_not_impl(
        self, mock_inverter_platform, mock_config_entry, mock_coordinator
    ):
        """Test ActivePowerLimitSet with SunSpec not implemented value."""
        mock_inverter_platform.decoded_model["I_Power_Limit"] = SunSpecNotImpl.UINT16
        entity = SolarEdgeActivePowerLimitSet(
            mock_inverter_platform, mock_config_entry, mock_coordinator
        )
        assert entity.available is False
