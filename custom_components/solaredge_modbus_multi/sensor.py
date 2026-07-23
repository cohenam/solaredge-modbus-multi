from __future__ import annotations

import datetime
import logging
import re

from awesomeversion import AwesomeVersion
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolarEdgeConfigEntry
from .const import (
    BATTERY_STATUS,
    BATTERY_STATUS_TEXT,
    DEVICE_STATUS,
    DEVICE_STATUS_TEXT,
    ENERGY_VOLT_AMPERE_HOUR,
    ENERGY_VOLT_AMPERE_REACTIVE_HOUR,
    INVERTED_POWER_VERSION,
    METER_EVENTS,
    MMPPT_EVENTS,
    RRCR_STATUS,
    SUNSPEC_DID,
    SUNSPEC_SF_RANGE,
    VENDOR4_STATUS,
    VENDOR_STATUS,
    BatteryLimit,
    SunSpecAccum,
    SunSpecNotImpl,
)
from .entity import SolarEdgeEntityBase
from .helpers import float_to_hex, is_float32_not_impl, update_accum

_LOGGER = logging.getLogger(__name__)


def _import_export_icon(phase: str | None) -> str | None:
    """Icon for import/export energy accumulators, or None for other phases."""
    if phase is None:
        return None

    if re.match("import", phase.lower()):
        return "mdi:transmission-tower-export"

    if re.match("export", phase.lower()):
        return "mdi:transmission-tower-import"

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SolarEdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = config_entry.runtime_data.hub
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    for inverter in hub.inverters:
        entities.append(SolarEdgeLastUpdate(inverter, config_entry, coordinator))
        entities.append(SolarEdgeDevice(inverter, config_entry, coordinator))
        entities.append(Version(inverter, config_entry, coordinator))
        entities.append(SolarEdgeInverterStatus(inverter, config_entry, coordinator))
        entities.append(StatusVendor(inverter, config_entry, coordinator))
        if inverter.use_status_vendor4:
            entities.append(StatusVendor4(inverter, config_entry, coordinator))
        entities.append(ACCurrentSensor(inverter, config_entry, coordinator))
        entities.append(ACCurrentSensor(inverter, config_entry, coordinator, "A"))
        entities.append(ACCurrentSensor(inverter, config_entry, coordinator, "B"))
        entities.append(ACCurrentSensor(inverter, config_entry, coordinator, "C"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "AB"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "BC"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "CA"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "AN"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "BN"))
        entities.append(VoltageSensor(inverter, config_entry, coordinator, "CN"))
        entities.append(ACPower(inverter, config_entry, coordinator))
        entities.append(ACFrequency(inverter, config_entry, coordinator))
        entities.append(ACVoltAmp(inverter, config_entry, coordinator))
        entities.append(ACVoltAmpReactive(inverter, config_entry, coordinator))
        entities.append(ACPowerFactor(inverter, config_entry, coordinator))
        entities.append(SolarEdgeACEnergy(inverter, config_entry, coordinator))
        entities.append(DCCurrent(inverter, config_entry, coordinator))
        entities.append(DCVoltage(inverter, config_entry, coordinator))
        entities.append(DCPower(inverter, config_entry, coordinator))
        entities.append(HeatSinkTemperature(inverter, config_entry, coordinator))

        if hub.option_detect_extras and inverter.global_power_control:
            entities.append(SolarEdgeRRCR(inverter, config_entry, coordinator))
            entities.append(
                SolarEdgeActivePowerLimit(inverter, config_entry, coordinator)
            )
            entities.append(SolarEdgeCosPhi(inverter, config_entry, coordinator))

        if hub.option_detect_extras and inverter.advanced_power_control:
            entities.append(
                SolarEdgeCommitControlSettings(inverter, config_entry, coordinator)
            )
            entities.append(
                SolarEdgeDefaultControlSettings(inverter, config_entry, coordinator)
            )

        if inverter.is_mmppt:
            entities.append(SolarEdgeMMPPTEvents(inverter, config_entry, coordinator))

            for mmppt_unit in inverter.mmppt_units:
                entities.append(
                    SolarEdgeDCCurrentMMPPT(mmppt_unit, config_entry, coordinator)
                )
                entities.append(
                    SolarEdgeDCVoltageMMPPT(mmppt_unit, config_entry, coordinator)
                )
                entities.append(
                    SolarEdgeDCPowerMMPPT(mmppt_unit, config_entry, coordinator)
                )
                entities.append(
                    SolarEdgeTemperatureMMPPT(mmppt_unit, config_entry, coordinator)
                )

    for meter in hub.meters:
        entities.append(SolarEdgeLastUpdate(meter, config_entry, coordinator))
        entities.append(SolarEdgeDevice(meter, config_entry, coordinator))
        entities.append(Version(meter, config_entry, coordinator))
        entities.append(MeterEvents(meter, config_entry, coordinator))
        entities.append(ACCurrentSensor(meter, config_entry, coordinator))
        entities.append(ACCurrentSensor(meter, config_entry, coordinator, "A"))
        entities.append(ACCurrentSensor(meter, config_entry, coordinator, "B"))
        entities.append(ACCurrentSensor(meter, config_entry, coordinator, "C"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "LN"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "AN"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "BN"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "CN"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "LL"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "AB"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "BC"))
        entities.append(VoltageSensor(meter, config_entry, coordinator, "CA"))
        entities.append(ACFrequency(meter, config_entry, coordinator))
        entities.append(ACPower(meter, config_entry, coordinator))
        entities.append(ACPowerInverted(meter, config_entry, coordinator))
        entities.append(ACPower(meter, config_entry, coordinator, "A"))
        entities.append(ACPower(meter, config_entry, coordinator, "B"))
        entities.append(ACPower(meter, config_entry, coordinator, "C"))
        entities.append(ACVoltAmp(meter, config_entry, coordinator))
        entities.append(ACVoltAmp(meter, config_entry, coordinator, "A"))
        entities.append(ACVoltAmp(meter, config_entry, coordinator, "B"))
        entities.append(ACVoltAmp(meter, config_entry, coordinator, "C"))
        entities.append(ACVoltAmpReactive(meter, config_entry, coordinator))
        entities.append(ACVoltAmpReactive(meter, config_entry, coordinator, "A"))
        entities.append(ACVoltAmpReactive(meter, config_entry, coordinator, "B"))
        entities.append(ACVoltAmpReactive(meter, config_entry, coordinator, "C"))
        entities.append(ACPowerFactor(meter, config_entry, coordinator))
        entities.append(ACPowerFactor(meter, config_entry, coordinator, "A"))
        entities.append(ACPowerFactor(meter, config_entry, coordinator, "B"))
        entities.append(ACPowerFactor(meter, config_entry, coordinator, "C"))
        entities.append(SolarEdgeACEnergy(meter, config_entry, coordinator, "Exported"))
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Exported_A")
        )
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Exported_B")
        )
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Exported_C")
        )
        entities.append(SolarEdgeACEnergy(meter, config_entry, coordinator, "Imported"))
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Imported_A")
        )
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Imported_B")
        )
        entities.append(
            SolarEdgeACEnergy(meter, config_entry, coordinator, "Imported_C")
        )
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Exported"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Exported_A"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Exported_B"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Exported_C"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Imported"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Imported_A"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Imported_B"))
        entities.append(MeterVAhIE(meter, config_entry, coordinator, "Imported_C"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q1"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q1_A"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q1_B"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q1_C"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q2"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q2_A"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q2_B"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Import_Q2_C"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q3"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q3_A"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q3_B"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q3_C"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q4"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q4_A"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q4_B"))
        entities.append(MetervarhIE(meter, config_entry, coordinator, "Export_Q4_C"))

    for battery in hub.batteries:
        entities.append(SolarEdgeLastUpdate(battery, config_entry, coordinator))
        entities.append(SolarEdgeDevice(battery, config_entry, coordinator))
        entities.append(Version(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryAvgTemp(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryMaxTemp(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryVoltage(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryCurrent(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryPower(battery, config_entry, coordinator))
        entities.append(
            SolarEdgeBatteryPowerInverted(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryEnergyExport(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryEnergyImport(battery, config_entry, coordinator)
        )
        entities.append(SolarEdgeBatteryMaxEnergy(battery, config_entry, coordinator))
        entities.append(
            SolarEdgeBatteryMaxChargePower(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryMaxDischargePower(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryMaxChargePeakPower(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryMaxDischargePeakPower(battery, config_entry, coordinator)
        )
        entities.append(
            SolarEdgeBatteryAvailableEnergy(battery, config_entry, coordinator)
        )
        entities.append(SolarEdgeBatterySOH(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatterySOE(battery, config_entry, coordinator))
        entities.append(SolarEdgeBatteryStatus(battery, config_entry, coordinator))

    for evse in hub.evses:
        entities.append(Version(evse, config_entry, coordinator))

    if entities:
        async_add_entities(entities)


class SolarEdgeSensorBase(SolarEdgeEntityBase, SensorEntity):
    def scale_factor(self, x: int, y: int):
        return x * (10**y)

    def sf_precision(self, model: dict, sf_key: str):
        """Display precision from a scale factor, or None if not implemented."""
        try:
            sf = model[sf_key]
        except (KeyError, TypeError):
            return None

        if sf == SunSpecNotImpl.INT16 or sf not in SUNSPEC_SF_RANGE:
            return None

        return abs(min(sf, 0))

    def scaled_or_none(self, value_key: str, sf_key: str, not_impl, model=None):
        """Scaled register value, or None when value or SF is not implemented."""
        if model is None:
            model = self._platform.decoded_model

        try:
            value = model[value_key]
            sf = model[sf_key]

            if (
                value == not_impl
                or sf == SunSpecNotImpl.INT16
                or sf not in SUNSPEC_SF_RANGE
            ):
                return None

            return self.scale_factor(value, sf)

        except (TypeError, KeyError):
            return None


class SolarEdgeDevice(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Device"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_device"

    @property
    def native_value(self):
        return self._platform.model

    @property
    def extra_state_attributes(self):
        attrs = {}

        try:
            if (
                not is_float32_not_impl(self._platform.decoded_common["B_RatedEnergy"])
                and self._platform.decoded_common["B_RatedEnergy"] > 0
            ):
                attrs["batt_rated_energy"] = self._platform.decoded_common[
                    "B_RatedEnergy"
                ]

        except KeyError:
            pass

        attrs["device_id"] = self._platform.device_address
        attrs["manufacturer"] = self._platform.manufacturer
        attrs["model"] = self._platform.model

        if len(self._platform.option) > 0:
            attrs["option"] = self._platform.option

        if self._platform.has_parent:
            attrs["parent_device_id"] = self._platform.inverter_unit_id

        attrs["serial_number"] = self._platform.serial

        try:
            if self._platform.decoded_model["C_SunSpec_DID"] in SUNSPEC_DID:
                attrs["sunspec_device"] = SUNSPEC_DID[
                    self._platform.decoded_model["C_SunSpec_DID"]
                ]

        except KeyError:
            pass

        try:
            attrs["sunspec_did"] = self._platform.decoded_model["C_SunSpec_DID"]

        except KeyError:
            pass

        try:
            if self._platform.decoded_mmppt is not None:
                try:
                    if self._platform.decoded_mmppt["mmppt_DID"] in SUNSPEC_DID:
                        attrs["mmppt_device"] = SUNSPEC_DID[
                            self._platform.decoded_mmppt["mmppt_DID"]
                        ]

                except KeyError:
                    pass

                attrs["mmppt_did"] = self._platform.decoded_mmppt["mmppt_DID"]
                attrs["mmppt_units"] = self._platform.decoded_mmppt["mmppt_Units"]

        except AttributeError:
            pass

        return attrs


class Version(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Version"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_version"

    @property
    def native_value(self):
        return self._platform.fw_version


class ACCurrentSensor(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._platform.decoded_model["C_SunSpec_DID"] in [101, 102, 103]:
            self.SUNSPEC_NOT_IMPL = SunSpecNotImpl.UINT16
        elif self._platform.decoded_model["C_SunSpec_DID"] in [201, 202, 203, 204]:
            self.SUNSPEC_NOT_IMPL = SunSpecNotImpl.INT16
        else:
            raise RuntimeError(
                "ACCurrentSensor C_SunSpec_DID "
                f"{self._platform.decoded_model['C_SunSpec_DID']}"
            )

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_current"
            self._attr_name = "AC Current"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_current_{self._phase.lower()}"
            )
            self._attr_name = f"AC Current {self._phase.upper()}"

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self._phase is None:
            return True

        elif self._platform.decoded_model["C_SunSpec_DID"] in [
            103,
            203,
            204,
        ] and self._phase in [
            "A",
            "B",
            "C",
        ]:
            return True

        else:
            return False

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_Current"
        else:
            model_key = f"AC_Current_{self._phase.upper()}"

        return self.scaled_or_none(model_key, "AC_Current_SF", self.SUNSPEC_NOT_IMPL)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_Current_SF")


class VoltageSensor(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._platform.decoded_model["C_SunSpec_DID"] in [101, 102, 103]:
            self.SUNSPEC_NOT_IMPL = SunSpecNotImpl.UINT16
        elif self._platform.decoded_model["C_SunSpec_DID"] in [201, 202, 203, 204]:
            self.SUNSPEC_NOT_IMPL = SunSpecNotImpl.INT16
        else:
            raise RuntimeError(
                "ACCurrentSensor C_SunSpec_DID "
                f"{self._platform.decoded_model['C_SunSpec_DID']}"
            )

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_voltage"
            self._attr_name = "AC Voltage"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_voltage_{self._phase.lower()}"
            )
            self._attr_name = f"AC Voltage {self._phase.upper()}"

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self._phase is None:
            raise NotImplementedError

        elif self._phase in ["LN", "LL", "AB"]:
            return True

        elif self._platform.decoded_model["C_SunSpec_DID"] in [
            103,
            203,
            204,
        ] and self._phase in [
            "BC",
            "CA",
            "AN",
            "BN",
            "CN",
        ]:
            return True

        else:
            return False

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_Voltage"
        else:
            model_key = f"AC_Voltage_{self._phase.upper()}"

        return self.scaled_or_none(model_key, "AC_Voltage_SF", self.SUNSPEC_NOT_IMPL)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_Voltage_SF")


class ACPower(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:solar-power"

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_power"
            self._attr_name = "AC Power"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_power_{self._phase.lower()}"
            )
            self._attr_name = f"AC Power {self._phase.upper()}"

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self._phase is None:
            return True

        elif self._platform.decoded_model["C_SunSpec_DID"] in [
            203,
            204,
        ] and self._phase in [
            "A",
            "B",
            "C",
        ]:
            return True

        else:
            return False

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_Power"
        else:
            model_key = f"AC_Power_{self._phase.upper()}"

        try:
            if (
                self._platform.decoded_model[model_key] == SunSpecNotImpl.INT16
                or self._platform.decoded_model["AC_Power_SF"] == SunSpecNotImpl.INT16
            ):
                return None

            else:
                return self.scale_factor(
                    self._platform.decoded_model[model_key],
                    self._platform.decoded_model["AC_Power_SF"],
                )

        except TypeError:
            return None

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_Power_SF")


class ACPowerInverted(ACPower):
    """Inverted AC power sensor for HA 2025.12 energy dashboard.

    HA defines grid power as positive=import, negative=export.
    SolarEdge meters use opposite convention. This class negates values.
    """

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator, phase)

        self._attr_unique_id = f"{self._attr_unique_id}_inverted"
        self._attr_name = f"{self._attr_name} Inverted"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return AwesomeVersion(HA_VERSION) < AwesomeVersion(INVERTED_POWER_VERSION)

    @property
    def native_value(self):
        value = super().native_value
        return None if value is None else -value


class ACFrequency(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_name = "AC Frequency"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_ac_frequency"

    @property
    def native_value(self):
        return self.scaled_or_none(
            "AC_Frequency", "AC_Frequency_SF", SunSpecNotImpl.UINT16
        )

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_Frequency_SF")


class ACVoltAmp(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.APPARENT_POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfApparentPower.VOLT_AMPERE
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_va"
            self._attr_name = "AC Apparent Power"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_va_{self._phase.lower()}"
            )
            self._attr_name = f"AC Apparent Power {self._phase.upper()}"

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_VA"
        else:
            model_key = f"AC_VA_{self._phase.upper()}"

        return self.scaled_or_none(model_key, "AC_VA_SF", SunSpecNotImpl.INT16)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_VA_SF")


class ACVoltAmpReactive(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.REACTIVE_POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfReactivePower.VOLT_AMPERE_REACTIVE
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_var"
            self._attr_name = "AC Reactive Power"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_var_{self._phase.lower()}"
            )
            self._attr_name = f"AC Reactive Power {self._phase.upper()}"

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_var"
        else:
            model_key = f"AC_var_{self._phase.upper()}"

        return self.scaled_or_none(model_key, "AC_var_SF", SunSpecNotImpl.INT16)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_var_SF")


class ACPowerFactor(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase

        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_pf"
            self._attr_name = "AC Power Factor"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_ac_pf_{self._phase.lower()}"
            )
            self._attr_name = f"AC Power Factor {self._phase.upper()}"

    @property
    def native_value(self):
        if self._phase is None:
            model_key = "AC_PF"
        else:
            model_key = f"AC_PF_{self._phase.upper()}"

        return self.scaled_or_none(model_key, "AC_PF_SF", SunSpecNotImpl.INT16)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "AC_PF_SF")


class SolarEdgeACEnergy(SolarEdgeSensorBase):
    """SolarEdge sensor for AC Energy watt-hour meters."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase
        self._last = None
        self._value = None
        self._log_once = False

        if self._phase is None:
            self._model_key = "AC_Energy_WH"
        else:
            self._model_key = f"AC_Energy_WH_{self._phase}"

        self._attr_icon = _import_export_icon(self._phase)

        # older versions of the integration converted to kWh internally
        # before home assistant had UI configurable units and precision
        # changing the unique_id now would cause new entities to be created
        if self._phase is None:
            self._attr_unique_id = f"{self._platform.uid_base}_ac_energy_kwh"
            self._attr_name = "AC Energy"
        else:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_{self._phase.lower()}_kwh"
            )
            self._attr_name = f"AC Energy {re.sub('_', ' ', self._phase)}"

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self._phase is None or self._phase in [
            "Exported",
            "Imported",
            "Exported_A",
            "Imported_A",
        ]:
            return True

        if self._platform.decoded_model["C_SunSpec_DID"] in [
            203,
            204,
        ] and self._phase in [
            "Exported_B",
            "Exported_C",
            "Imported_B",
            "Imported_C",
        ]:
            return True

        return False

    @property
    def available(self) -> bool:
        try:
            if (
                self._platform.decoded_model[self._model_key] == SunSpecAccum.NA32
                or self._platform.decoded_model[self._model_key] > SunSpecAccum.LIMIT32
                or self._platform.decoded_model["AC_Energy_WH_SF"]
                not in SUNSPEC_SF_RANGE
            ):
                return False

            if self._last is None:
                self._last = 0

            self._value = self.scale_factor(
                self._platform.decoded_model[self._model_key],
                self._platform.decoded_model["AC_Energy_WH_SF"],
            )

            if self._value < self._last:
                if not self._log_once:
                    _LOGGER.warning(
                        "Inverter accumulator went backwards; this is a SolarEdge bug: "
                        f"{self._model_key} {self._value} < {self._last}"
                    )
                    self._log_once = True

                return False

        except KeyError:
            return False

        except (ZeroDivisionError, OverflowError) as e:
            _LOGGER.debug(f"total_increasing {self._model_key} exception: {e}")
            return False

        self._log_once = False
        return super().available

    @property
    def native_value(self):
        self._last = self._value
        return self._value


class DCCurrent(SolarEdgeSensorBase):
    """DC Current for a SolarEdge inverter."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:current-dc"
    _attr_name = "DC Current"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_current"

    @property
    def available(self) -> bool:
        if (
            self._platform.decoded_model["I_DC_Current"] == SunSpecNotImpl.UINT16
            or self._platform.decoded_model["I_DC_Current_SF"] == SunSpecNotImpl.INT16
            or self._platform.decoded_model["I_DC_Current_SF"] not in SUNSPEC_SF_RANGE
        ):
            return False

        return super().available

    @property
    def native_value(self):
        try:
            return self.scale_factor(
                self._platform.decoded_model["I_DC_Current"],
                self._platform.decoded_model["I_DC_Current_SF"],
            )

        except TypeError:
            return None

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "I_DC_Current_SF")


class SolarEdgeDCCurrentMMPPT(SolarEdgeSensorBase):
    """DC Current for Synergy MMPPT units."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:current-dc"
    _attr_name = "DC Current"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = (
            f"{self._platform.inverter.uid_base}_dc_current_mmppt{self._platform.unit}"
        )

    @property
    def available(self) -> bool:
        if (
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCA"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCA_SF"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCA_SF"]
            not in SUNSPEC_SF_RANGE
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self.scale_factor(
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCA"],
            self._platform.inverter.decoded_model["mmppt_DCA_SF"],
        )

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.inverter.decoded_model, "mmppt_DCA_SF")


class DCVoltage(SolarEdgeSensorBase):
    """DC Voltage for a SolarEdge inverter."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_name = "DC Voltage"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_voltage"

    @property
    def native_value(self):
        return self.scaled_or_none(
            "I_DC_Voltage", "I_DC_Voltage_SF", SunSpecNotImpl.UINT16
        )

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "I_DC_Voltage_SF")


class SolarEdgeDCVoltageMMPPT(SolarEdgeSensorBase):
    """DC Voltage for Synergy MMPPT units."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_name = "DC Voltage"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = (
            f"{self._platform.inverter.uid_base}_dc_voltage_mmppt{self._platform.unit}"
        )

    @property
    def available(self) -> bool:
        if (
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCV"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCV_SF"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCV_SF"]
            not in SUNSPEC_SF_RANGE
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self.scale_factor(
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCV"],
            self._platform.inverter.decoded_model["mmppt_DCV_SF"],
        )

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.inverter.decoded_model, "mmppt_DCV_SF")


class DCPower(SolarEdgeSensorBase):
    """DC Power for a SolarEdge inverter."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:solar-power"
    _attr_name = "DC Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_power"

    @property
    def native_value(self):
        return self.scaled_or_none("I_DC_Power", "I_DC_Power_SF", SunSpecNotImpl.INT16)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "I_DC_Power_SF")


class SolarEdgeDCPowerMMPPT(SolarEdgeSensorBase):
    """DC Power for Synergy MMPPT units."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:solar-power"
    _attr_name = "DC Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = (
            f"{self._platform.inverter.uid_base}_dc_power_mmppt{self._platform.unit}"
        )

    @property
    def available(self) -> bool:
        if (
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCW"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCW_SF"]
            == SunSpecNotImpl.INT16
            or self._platform.inverter.decoded_model["mmppt_DCW_SF"]
            not in SUNSPEC_SF_RANGE
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self.scale_factor(
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["DCW"],
            self._platform.inverter.decoded_model["mmppt_DCW_SF"],
        )

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.inverter.decoded_model, "mmppt_DCW_SF")


class HeatSinkTemperature(SolarEdgeSensorBase):
    """Heat sink temperature for a SolarEdge inverter."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_name = "Temperature"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_temp_sink"

    @property
    def native_value(self):
        return self.scaled_or_none("I_Temp_Sink", "I_Temp_SF", SunSpecNotImpl.INT16)

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "I_Temp_SF")


class SolarEdgeTemperatureMMPPT(SolarEdgeSensorBase):
    """Temperature for Synergy MMPPT units."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 0
    _attr_name = "Temperature"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = (
            f"{self._platform.inverter.uid_base}_tmp_mmppt{self._platform.unit}"
        )

    @property
    def available(self) -> bool:
        if (
            self._platform.inverter.decoded_model[self._platform.mmppt_key]["Tmp"]
            == SunSpecNotImpl.INT16
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.inverter.decoded_model[self._platform.mmppt_key]["Tmp"]


class SolarEdgeStatusSensor(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Status"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_status"


class SolarEdgeInverterStatus(SolarEdgeStatusSensor):
    _attr_options = list(DEVICE_STATUS.values())

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["I_Status"] == SunSpecNotImpl.UINT16:
                return None

            return str(DEVICE_STATUS[self._platform.decoded_model["I_Status"]])

        except TypeError:
            return None

        except KeyError:
            return None

    @property
    def extra_state_attributes(self):
        attrs = {}

        try:
            if self._platform.decoded_model["I_Status"] in DEVICE_STATUS_TEXT:
                attrs["status_text"] = DEVICE_STATUS_TEXT[
                    self._platform.decoded_model["I_Status"]
                ]

                attrs["status_value"] = self._platform.decoded_model["I_Status"]

        except KeyError:
            pass

        return attrs


class SolarEdgeBatteryStatus(SolarEdgeStatusSensor):
    _attr_options = list(BATTERY_STATUS.values())

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["B_Status"] == SunSpecNotImpl.UINT32:
                return None

            return str(BATTERY_STATUS[self._platform.decoded_model["B_Status"]])

        except TypeError:
            return None

        except KeyError:
            return None

    @property
    def extra_state_attributes(self):
        attrs = {}

        try:
            if self._platform.decoded_model["B_Status"] in BATTERY_STATUS_TEXT:
                attrs["status_text"] = BATTERY_STATUS_TEXT[
                    self._platform.decoded_model["B_Status"]
                ]

            attrs["status_value"] = self._platform.decoded_model["B_Status"]

        except KeyError:
            pass

        return attrs


class StatusVendor(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Status Vendor"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_status_vendor"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return not self._platform.use_status_vendor4

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["I_Status_Vendor"] == SunSpecNotImpl.UINT16:
                return None

            else:
                return str(self._platform.decoded_model["I_Status_Vendor"])

        except TypeError:
            return None

    @property
    def extra_state_attributes(self):
        try:
            if self._platform.decoded_model["I_Status_Vendor"] in VENDOR_STATUS:
                return {
                    "description": VENDOR_STATUS[
                        self._platform.decoded_model["I_Status_Vendor"]
                    ]
                }

            else:
                return None

        except KeyError:
            return None


class StatusVendor4(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Status Vendor 4"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_status_vendor4"

    @property
    def available(self) -> bool:
        return (
            super().available
            and "I_Status_Vendor4" in self._platform.decoded_model
            and self._platform.decoded_model["I_Status_Vendor4"]
            != SunSpecNotImpl.UINT32
        )

    @property
    def native_value(self):
        try:
            value = self._platform.decoded_model["I_Status_Vendor4"]
            controller = (value >> 24) & 0xFF
            error = value & 0xFFFF
            return f"{controller:X}x{error:X}"
        except TypeError:
            return None

    @property
    def extra_state_attributes(self):
        try:
            value = self._platform.decoded_model["I_Status_Vendor4"]

            controller = (value >> 24) & 0xFF
            error = value & 0xFFFF
            attrs = {
                "controller": hex(controller),
                "error_code": hex(error),
            }

            if controller in VENDOR4_STATUS and error in VENDOR4_STATUS[controller]:
                attrs["description"] = VENDOR4_STATUS[controller][error]

            return attrs

        except KeyError:
            return None

        except TypeError:
            return None


class SolarEdgeGlobalPowerControlBlock(SolarEdgeSensorBase):
    @property
    def available(self) -> bool:
        return super().available and self._platform.global_power_control


class SolarEdgeRRCR(SolarEdgeGlobalPowerControlBlock):
    _attr_name = "RRCR Status"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_rrcr"

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self._platform.global_power_control is True:
            return True
        else:
            return False

    @property
    def native_value(self):
        try:
            if (
                self._platform.decoded_model["I_RRCR"] == SunSpecNotImpl.UINT16
                or self._platform.decoded_model["I_RRCR"] > 0xF
            ):
                return None

            else:
                return self._platform.decoded_model["I_RRCR"]

        except TypeError:
            return None

        except KeyError:
            return None

    @property
    def extra_state_attributes(self):
        try:
            rrcr_inputs = []

            if int(str(self._platform.decoded_model["I_RRCR"])) == 0x0:
                return {"inputs": str(rrcr_inputs)}

            else:
                for i in range(0, 4):
                    if int(str(self._platform.decoded_model["I_RRCR"])) & (1 << i):
                        rrcr_inputs.append(RRCR_STATUS[i])

                return {"inputs": str(rrcr_inputs)}

        except KeyError:
            return None


class SolarEdgeActivePowerLimit(SolarEdgeGlobalPowerControlBlock):
    """Global Dynamic Power Control: Inverter Active Power Limit"""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:percent"
    _attr_name = "Active Power Limit"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_active_power_limit"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return self._platform.global_power_control

    @property
    def native_value(self) -> int:
        try:
            if (
                self._platform.decoded_model["I_Power_Limit"] == SunSpecNotImpl.UINT16
                or self._platform.decoded_model["I_Power_Limit"] > 100
                or self._platform.decoded_model["I_Power_Limit"] < 0
            ):
                return None

            else:
                return self._platform.decoded_model["I_Power_Limit"]

        except KeyError:
            return None


class SolarEdgeCosPhi(SolarEdgeGlobalPowerControlBlock):
    """Global Dynamic Power Control: Inverter CosPhi"""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:angle-acute"
    _attr_name = "CosPhi"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_cosphi"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return self._platform.global_power_control

    @property
    def native_value(self) -> float:
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["I_CosPhi"])
                or self._platform.decoded_model["I_CosPhi"] > 1.0
                or self._platform.decoded_model["I_CosPhi"] < -1.0
            ):
                return None

            else:
                return round(self._platform.decoded_model["I_CosPhi"], 1)

        except KeyError:
            return None


class MeterEvents(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Meter Events"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_meter_events"

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["M_Events"] == SunSpecNotImpl.UINT32:
                return None

            else:
                return self._platform.decoded_model["M_Events"]

        except TypeError:
            return None

    @property
    def extra_state_attributes(self):
        attrs = {}
        m_events_active = []

        if int(str(self._platform.decoded_model["M_Events"])) == 0x0:
            attrs["events"] = str(m_events_active)
        else:
            for i in range(2, 31):
                try:
                    if int(str(self._platform.decoded_model["M_Events"])) & (1 << i):
                        m_events_active.append(METER_EVENTS[i])

                except KeyError:
                    pass

        attrs["bits"] = f"{int(self._platform.decoded_model['M_Events']):032b}"
        attrs["events"] = str(m_events_active)

        return attrs


class SolarEdgeMMPPTEvents(SolarEdgeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "MMPPT Events"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_mmppt_events"

    @property
    def available(self) -> bool:
        try:
            if self._platform.decoded_model["mmppt_Events"] == SunSpecNotImpl.UINT32:
                return False

            return super().available

        except KeyError:
            return False

    @property
    def native_value(self) -> int:
        return self._platform.decoded_model["mmppt_Events"]

    @property
    def extra_state_attributes(self) -> str:
        attrs = {}
        mmppt_events_active = []

        if int(str(self._platform.decoded_model["mmppt_Events"])) == 0x0:
            attrs["events"] = str(mmppt_events_active)
        else:
            for i in range(0, 31):
                try:
                    if int(str(self._platform.decoded_model["mmppt_Events"])) & (
                        1 << i
                    ):
                        mmppt_events_active.append(MMPPT_EVENTS[i])
                except KeyError:
                    pass

        attrs["events"] = str(mmppt_events_active)
        attrs["bits"] = f"{int(self._platform.decoded_model['mmppt_Events']):032b}"

        return attrs


class MeterVAhIE(SolarEdgeSensorBase):
    # No HA device class accepts VAh/varh units
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = ENERGY_VOLT_AMPERE_HOUR
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase
        self.last = None

        if self._phase is None:
            raise NotImplementedError

        self._attr_unique_id = f"{self._platform.uid_base}_{self._phase.lower()}_vah"
        self._attr_name = f"Apparent Energy {re.sub('_', ' ', self._phase)}"
        self._attr_icon = _import_export_icon(self._phase)

    @property
    def native_value(self):
        if self._phase is None:
            raise NotImplementedError
        else:
            model_key = f"M_VAh_{self._phase}"

        try:
            if (
                self._platform.decoded_model[model_key] == SunSpecAccum.NA32
                or self._platform.decoded_model[model_key] > SunSpecAccum.LIMIT32
                or self._platform.decoded_model["M_VAh_SF"] == SunSpecNotImpl.INT16
                or self._platform.decoded_model["M_VAh_SF"] not in SUNSPEC_SF_RANGE
            ):
                return None

            else:
                value = self.scale_factor(
                    self._platform.decoded_model[model_key],
                    self._platform.decoded_model["M_VAh_SF"],
                )

                try:
                    return update_accum(self, value)
                except Exception:
                    return None

        except TypeError:
            return None

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "M_VAh_SF")


class MetervarhIE(SolarEdgeSensorBase):
    # No HA device class accepts VAh/varh units
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = ENERGY_VOLT_AMPERE_REACTIVE_HOUR
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase
        self.last = None

        if self._phase is None:
            raise NotImplementedError

        self._attr_unique_id = f"{self._platform.uid_base}_{self._phase.lower()}_varh"
        self._attr_name = f"Reactive Energy {re.sub('_', ' ', self._phase)}"
        self._attr_icon = _import_export_icon(self._phase)

    @property
    def native_value(self):
        if self._phase is None:
            raise NotImplementedError
        else:
            model_key = f"M_varh_{self._phase}"

        try:
            if (
                self._platform.decoded_model[model_key] == SunSpecAccum.NA32
                or self._platform.decoded_model[model_key] > SunSpecAccum.LIMIT32
                or self._platform.decoded_model["M_varh_SF"] == SunSpecNotImpl.INT16
                or self._platform.decoded_model["M_varh_SF"] not in SUNSPEC_SF_RANGE
            ):
                return None

            else:
                value = self.scale_factor(
                    self._platform.decoded_model[model_key],
                    self._platform.decoded_model["M_varh_SF"],
                )

                try:
                    return update_accum(self, value)
                except Exception:
                    return None

        except TypeError:
            return None

    @property
    def suggested_display_precision(self):
        return self.sf_precision(self._platform.decoded_model, "M_varh_SF")


class SolarEdgeBatteryAvgTemp(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_name = "Average Temperature"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_avg_temp"

    @property
    def native_value(self):
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["B_Temp_Average"])
                or self._platform.decoded_model["B_Temp_Average"] < BatteryLimit.Tmin
                or self._platform.decoded_model["B_Temp_Average"] > BatteryLimit.Tmax
            ):
                return None

            else:
                return self._platform.decoded_model["B_Temp_Average"]

        except TypeError:
            return None


class SolarEdgeBatteryMaxTemp(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_name = "Max Temperature"
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_temp"

    @property
    def native_value(self):
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["B_Temp_Max"])
                or self._platform.decoded_model["B_Temp_Max"] < BatteryLimit.Tmin
                or self._platform.decoded_model["B_Temp_Max"] > BatteryLimit.Tmax
            ):
                return None

            else:
                return self._platform.decoded_model["B_Temp_Max"]

        except TypeError:
            return None


class SolarEdgeBatteryVoltage(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 2
    _attr_name = "DC Voltage"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_voltage"

    @property
    def native_value(self):
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["B_DC_Voltage"])
                or self._platform.decoded_model["B_DC_Voltage"] < BatteryLimit.Vmin
                or self._platform.decoded_model["B_DC_Voltage"] > BatteryLimit.Vmax
            ):
                return None

            elif self._platform.decoded_model["B_Status"] in [0]:
                return None

            else:
                return self._platform.decoded_model["B_DC_Voltage"]

        except TypeError:
            return None


class SolarEdgeBatteryCurrent(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:current-dc"
    _attr_name = "DC Current"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_current"

    @property
    def available(self) -> bool:
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["B_DC_Current"])
                or self._platform.decoded_model["B_DC_Current"] < BatteryLimit.Amin
                or self._platform.decoded_model["B_DC_Current"] > BatteryLimit.Amax
            ):
                return False

            if self._platform.decoded_model["B_Status"] in [0]:
                return False

            return super().available

        except (TypeError, KeyError):
            return False

    @property
    def native_value(self):
        return self._platform.decoded_model["B_DC_Current"]


class SolarEdgeBatteryPower(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:lightning-bolt"
    _attr_name = "DC Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_dc_power"

    @property
    def native_value(self):
        try:
            if (
                is_float32_not_impl(self._platform.decoded_model["B_DC_Power"])
                or float_to_hex(self._platform.decoded_model["B_DC_Power"])
                == "0xff7fffff"
                or float_to_hex(self._platform.decoded_model["B_DC_Power"])
                == "0x7f7fffff"
            ):
                return None

            elif self._platform.decoded_model["B_Status"] in [0]:
                return None

            else:
                return self._platform.decoded_model["B_DC_Power"]

        except TypeError:
            return None


class SolarEdgeBatteryPowerInverted(SolarEdgeBatteryPower):
    """Inverted battery power sensor for HA 2025.12 energy dashboard."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

        self._attr_unique_id = f"{self._attr_unique_id}_inverted"
        self._attr_name = f"{self._attr_name} Inverted"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return AwesomeVersion(HA_VERSION) < AwesomeVersion(INVERTED_POWER_VERSION)

    @property
    def native_value(self):
        value = super().native_value
        return None if value is None else -value


class SolarEdgeBatteryEnergyExport(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:battery-charging-20"
    _attr_name = "Energy Export"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

        self._last = None
        self._count = 0
        self._log_once = None
        self._attr_unique_id = f"{self._platform.uid_base}_energy_export"

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model[
                "B_Export_Energy_WH"
            ] == 0xFFFFFFFFFFFFFFFF or (
                self._platform.decoded_model["B_Export_Energy_WH"] == 0x0
                and not self._platform.allow_battery_energy_reset
            ):
                return None

            else:
                try:
                    if self._last is None:
                        self._last = 0

                    if self._platform.decoded_model["B_Export_Energy_WH"] >= self._last:
                        self._last = self._platform.decoded_model["B_Export_Energy_WH"]
                        self._log_once = False

                        if self._platform.allow_battery_energy_reset:
                            self._count = 0

                        return self._platform.decoded_model["B_Export_Energy_WH"]

                    else:
                        if (
                            not self._platform.allow_battery_energy_reset
                            and not self._log_once
                        ):
                            _LOGGER.warning(
                                (
                                    "Battery Export Energy went backwards: Current value "
                                    f"{self._platform.decoded_model['B_Export_Energy_WH']} "
                                    f"is less than last value of {self._last}"
                                )
                            )
                            self._log_once = True

                        if self._platform.allow_battery_energy_reset:
                            self._count += 1
                            _LOGGER.debug(
                                (
                                    "B_Export_Energy went backwards: "
                                    f"{self._platform.decoded_model['B_Export_Energy_WH']} "
                                    f"< {self._last} cycle {self._count} of "
                                    f"{self._platform.battery_energy_reset_cycles}"
                                )
                            )

                            if self._count > self._platform.battery_energy_reset_cycles:
                                _LOGGER.debug(
                                    f"B_Export_Energy reset at cycle {self._count}"
                                )
                                self._last = None
                                self._count = 0

                        return None

                except OverflowError:
                    return None

        except TypeError:
            return None


class SolarEdgeBatteryEnergyImport(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:battery-charging-100"
    _attr_name = "Energy Import"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

        self._last = None
        self._count = 0
        self._log_once = None
        self._attr_unique_id = f"{self._platform.uid_base}_energy_import"

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model[
                "B_Import_Energy_WH"
            ] == 0xFFFFFFFFFFFFFFFF or (
                self._platform.decoded_model["B_Import_Energy_WH"] == 0x0
                and not self._platform.allow_battery_energy_reset
            ):
                return None

            else:
                try:
                    if self._last is None:
                        self._last = 0

                    if self._platform.decoded_model["B_Import_Energy_WH"] >= self._last:
                        self._last = self._platform.decoded_model["B_Import_Energy_WH"]
                        self._log_once = False

                        if self._platform.allow_battery_energy_reset:
                            self._count = 0

                        return self._platform.decoded_model["B_Import_Energy_WH"]

                    else:
                        if (
                            not self._platform.allow_battery_energy_reset
                            and not self._log_once
                        ):
                            _LOGGER.warning(
                                (
                                    "Battery Import Energy went backwards: Current value "
                                    f"{self._platform.decoded_model['B_Import_Energy_WH']} "
                                    f"is less than last value of {self._last}"
                                )
                            )
                            self._log_once = True

                        if self._platform.allow_battery_energy_reset:
                            self._count += 1
                            _LOGGER.debug(
                                (
                                    "B_Import_Energy went backwards: "
                                    f"{self._platform.decoded_model['B_Import_Energy_WH']} "
                                    f"< {self._last} cycle {self._count} of "
                                    f"{self._platform.battery_energy_reset_cycles}"
                                )
                            )

                            if self._count > self._platform.battery_energy_reset_cycles:
                                _LOGGER.debug(
                                    f"B_Import_Energy reset at cycle {self._count}"
                                )
                                self._last = None
                                self._count = 0

                        return None

                except OverflowError:
                    return None

        except TypeError:
            return None


class SolarEdgeBatteryMaxEnergy(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_name = "Maximum Energy"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_energy"

    @property
    def native_value(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_Energy_Max"])
            or self._platform.decoded_model["B_Energy_Max"] < 0
            or self._platform.decoded_model["B_Energy_Max"]
            > self._platform.decoded_common["B_RatedEnergy"]
        ):
            return None

        else:
            return self._platform.decoded_model["B_Energy_Max"]


class SolarEdgeBatteryPowerBase(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0


class SolarEdgeBatteryMaxChargePower(SolarEdgeBatteryPowerBase):
    _attr_name = "Max Charge Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_charge_power"

    @property
    def available(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_MaxChargePower"])
            or self._platform.decoded_model["B_MaxChargePower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxChargePower"]


class SolarEdgeBatteryMaxChargePeakPower(SolarEdgeBatteryPowerBase):
    _attr_name = "Peak Charge Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_charge_peak_power"

    @property
    def available(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_MaxChargePeakPower"])
            or self._platform.decoded_model["B_MaxChargePeakPower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxChargePeakPower"]


class SolarEdgeBatteryMaxDischargePower(SolarEdgeBatteryPowerBase):
    _attr_name = "Max Discharge Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_discharge_power"

    @property
    def available(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_MaxDischargePower"])
            or self._platform.decoded_model["B_MaxDischargePower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxDischargePower"]


class SolarEdgeBatteryMaxDischargePeakPower(SolarEdgeBatteryPowerBase):
    _attr_name = "Peak Discharge Power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_max_discharge_peak_power"

    @property
    def available(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_MaxDischargePeakPower"])
            or self._platform.decoded_model["B_MaxDischargePeakPower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxDischargePeakPower"]


class SolarEdgeBatteryAvailableEnergy(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_name = "Available Energy"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._log_warning = True
        self._attr_unique_id = f"{self._platform.uid_base}_avail_energy"

    @property
    def native_value(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_Energy_Available"])
            or self._platform.decoded_model["B_Energy_Available"] < 0
        ):
            return None

        if self._platform.decoded_model["B_Energy_Available"] > (
            self._platform.decoded_common["B_RatedEnergy"]
            * self._platform.battery_rating_adjust
        ):
            if self._log_warning:
                _LOGGER.warning(
                    f"I{self._platform.inverter_unit_id}B{self._platform.battery_id}: "
                    "Battery available energy exceeds rated energy. "
                    "Set configuration for Battery Rating Adjustment when necessary."
                )
                self._log_warning = False

            return None

        else:
            return self._platform.decoded_model["B_Energy_Available"]


class SolarEdgeBatterySOH(SolarEdgeSensorBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-heart-outline"
    _attr_name = "State of Health"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_battery_soh"

    @property
    def native_value(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_SOH"])
            or self._platform.decoded_model["B_SOH"] < 0
            or self._platform.decoded_model["B_SOH"] > 100
        ):
            return None
        else:
            return self._platform.decoded_model["B_SOH"]


class SolarEdgeBatterySOE(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_name = "State of Energy"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_battery_soe"

    @property
    def native_value(self):
        if (
            is_float32_not_impl(self._platform.decoded_model["B_SOE"])
            or self._platform.decoded_model["B_SOE"] < 0
            or self._platform.decoded_model["B_SOE"] > 100
        ):
            return None
        else:
            return self._platform.decoded_model["B_SOE"]


class SolarEdgeAdvancedPowerControlBlock(SolarEdgeSensorBase):
    @property
    def available(self) -> bool:
        return super().available and self._platform.advanced_power_control


class SolarEdgeCommitControlSettings(SolarEdgeAdvancedPowerControlBlock):
    """Entity to show the results of Commit Power Control Settings button."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:content-save-cog-outline"
    _attr_name = "Commit Power Settings"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_commit_pwr_settings"

    @property
    def available(self) -> bool:
        return (
            super().available and "CommitPwrCtlSettings" in self._platform.decoded_model
        )

    @property
    def native_value(self):
        return self._platform.decoded_model["CommitPwrCtlSettings"]

    @property
    def extra_state_attributes(self):
        attrs = {}

        attrs["hex_value"] = hex(self._platform.decoded_model["CommitPwrCtlSettings"])

        if self._platform.decoded_model["CommitPwrCtlSettings"] == 0x0:
            attrs["status"] = "SUCCESS"
        if self._platform.decoded_model["CommitPwrCtlSettings"] in [0x1, 0x2, 0x3, 0x4]:
            attrs["status"] = "INTERNAL_ERROR"
        if self._platform.decoded_model["CommitPwrCtlSettings"] == 0xFFFF:
            attrs["status"] = "UNKNOWN_ERROR"
        if (
            self._platform.decoded_model["CommitPwrCtlSettings"] >= 0xF102
            and self._platform.decoded_model["CommitPwrCtlSettings"] < 0xFFFF
        ):
            attrs["status"] = "VALUE_ERROR"

        return attrs


class SolarEdgeDefaultControlSettings(SolarEdgeAdvancedPowerControlBlock):
    """Entity to show the results of Restore Power Control Default Settings button."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:restore-alert"
    _attr_name = "Default Power Settings"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_default_pwr_settings"

    @property
    def available(self) -> bool:
        return (
            super().available
            and "RestorePwrCtlDefaults" in self._platform.decoded_model
        )

    @property
    def native_value(self):
        return self._platform.decoded_model["RestorePwrCtlDefaults"]

    @property
    def extra_state_attributes(self):
        attrs = {}

        attrs["hex_value"] = hex(self._platform.decoded_model["RestorePwrCtlDefaults"])

        if self._platform.decoded_model["RestorePwrCtlDefaults"] == 0x0:
            attrs["status"] = "SUCCESS"
        if self._platform.decoded_model["RestorePwrCtlDefaults"] == 0xFFFF:
            attrs["status"] = "ERROR"

        return attrs


class SolarEdgeLastUpdate(SolarEdgeSensorBase):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last Update"
    _attr_entity_registry_enabled_default = False

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._attr_unique_id = f"{self._platform.uid_base}_last_update_timestamp"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime.datetime | None:
        return self._platform.last_update
