from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from awesomeversion import AwesomeVersion
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BATTERY_STATUS,
    BATTERY_STATUS_TEXT,
    DEVICE_STATUS,
    DEVICE_STATUS_TEXT,
    DOMAIN,
    ENERGY_VOLT_AMPERE_HOUR,
    ENERGY_VOLT_AMPERE_REACTIVE_HOUR,
    METER_EVENTS,
    MMPPT_EVENTS,
    RRCR_STATUS,
    SUNSPEC_DID,
    SUNSPEC_SF_RANGE,
    VENDOR_STATUS,
    BatteryLimit,
    SunSpecAccum,
    SunSpecNotImpl,
)
from .helpers import float_to_hex, update_accum

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity description dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SolarEdgeSensorEntityDescription(SensorEntityDescription):
    """Extended sensor description for SolarEdge Modbus sensors."""

    # Register key in decoded_model (value register)
    register_key: str
    # Scale factor key in decoded_model (None for float32/direct sensors)
    scale_factor_key: str | None = None
    # The SunSpecNotImpl sentinel to compare the value register against
    sunspec_not_impl: int = SunSpecNotImpl.INT16
    # Whether to also check SF against SUNSPEC_SF_RANGE
    check_sf_range: bool = True
    # Set True when sunspec_not_impl depends on C_SunSpec_DID (inverter vs meter)
    did_based_not_impl: bool = False
    # Phase suffix appended to register_key (e.g. "A" -> "AC_Current_A")
    phase: str | None = None
    # Unique-id suffix (the part after uid_base_). Phase is auto-appended.
    uid_suffix: str = ""
    # Static enabled_default (True/None/False). None means "decide at runtime".
    enabled_default: bool | None = True
    # Which DID values enable a phase sensor
    enabled_dids: tuple[int, ...] | None = None
    # Fixed display precision (overrides SF-derived)
    fixed_precision: int | None = None
    # Extra not-impl checks on the raw value (e.g. HeatSink also checks 0x0)
    extra_not_impl_values: tuple[int, ...] = ()


# ---------------------------------------------------------------------------
# Description tuples — Category A sensors
# ---------------------------------------------------------------------------


def _ac_current_descs() -> list[SolarEdgeSensorEntityDescription]:
    """ACCurrentSensor: phases None, A, B, C."""
    base = dict(
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        register_key="AC_Current",
        scale_factor_key="AC_Current_SF",
        did_based_not_impl=True,
        uid_suffix="ac_current",
    )
    descs = [
        SolarEdgeSensorEntityDescription(
            key="ac_current",
            **base,
            enabled_default=True,
        ),
    ]
    for ph in ("A", "B", "C"):
        descs.append(
            SolarEdgeSensorEntityDescription(
                key=f"ac_current_{ph.lower()}",
                **base,
                phase=ph,
                enabled_default=None,  # runtime: DID in [103,203,204]
                enabled_dids=(103, 203, 204),
            )
        )
    return descs


def _voltage_descs_inverter() -> list[SolarEdgeSensorEntityDescription]:
    """VoltageSensor for inverters: AB, BC, CA, AN, BN, CN."""
    base = dict(
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        register_key="AC_Voltage",
        scale_factor_key="AC_Voltage_SF",
        did_based_not_impl=True,
        uid_suffix="ac_voltage",
    )
    descs = []
    for ph in ("AB", "BC", "CA", "AN", "BN", "CN"):
        enabled = True if ph == "AB" else None
        descs.append(
            SolarEdgeSensorEntityDescription(
                key=f"ac_voltage_{ph.lower()}",
                **base,
                phase=ph,
                enabled_default=enabled,
                enabled_dids=(103, 203, 204),
            )
        )
    return descs


def _voltage_descs_meter() -> list[SolarEdgeSensorEntityDescription]:
    """VoltageSensor for meters: LN, AN, BN, CN, LL, AB, BC, CA."""
    base = dict(
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        register_key="AC_Voltage",
        scale_factor_key="AC_Voltage_SF",
        did_based_not_impl=True,
        uid_suffix="ac_voltage",
    )
    descs = []
    for ph in ("LN", "AN", "BN", "CN", "LL", "AB", "BC", "CA"):
        if ph in ("LN", "LL", "AB"):
            enabled = True
        else:
            enabled = None  # runtime check
        descs.append(
            SolarEdgeSensorEntityDescription(
                key=f"ac_voltage_{ph.lower()}",
                **base,
                phase=ph,
                enabled_default=enabled,
                enabled_dids=(103, 203, 204),
            )
        )
    return descs


def _ac_power_descs(with_phases: bool) -> list[SolarEdgeSensorEntityDescription]:
    """ACPower: phase=None always; A/B/C only when with_phases (meters)."""
    base = dict(
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        register_key="AC_Power",
        scale_factor_key="AC_Power_SF",
        sunspec_not_impl=SunSpecNotImpl.INT16,
        uid_suffix="ac_power",
    )
    descs = [
        SolarEdgeSensorEntityDescription(
            key="ac_power",
            icon="mdi:solar-power",
            **base,
            enabled_default=True,
        ),
    ]
    phases = ("A", "B", "C") if with_phases else ()
    for ph in phases:
        descs.append(
            SolarEdgeSensorEntityDescription(
                key=f"ac_power_{ph.lower()}",
                icon="mdi:solar-power",
                **base,
                phase=ph,
                enabled_default=None,
                enabled_dids=(203, 204),
            )
        )
    return descs


def _ac_freq_desc() -> SolarEdgeSensorEntityDescription:
    return SolarEdgeSensorEntityDescription(
        key="ac_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        register_key="AC_Frequency",
        scale_factor_key="AC_Frequency_SF",
        sunspec_not_impl=SunSpecNotImpl.UINT16,
        uid_suffix="ac_frequency",
    )


def _phase_descs(
    key_prefix: str,
    uid_prefix: str,
    register_prefix: str,
    sf_key: str,
    device_class: SensorDeviceClass,
    unit: str,
    phases: tuple[str, ...],
    name_prefix: str,  # unused in descriptions but kept for reference
) -> list[SolarEdgeSensorEntityDescription]:
    """Generic helper for ACVoltAmp, ACVoltAmpReactive, ACPowerFactor."""
    descs = []
    base = dict(
        device_class=device_class,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=unit,
        register_key=register_prefix,
        scale_factor_key=sf_key,
        sunspec_not_impl=SunSpecNotImpl.INT16,
        uid_suffix=uid_prefix,
        enabled_default=False,
    )
    for ph in phases:
        descs.append(
            SolarEdgeSensorEntityDescription(
                key=f"{key_prefix}_{ph.lower()}" if ph else key_prefix,
                **base,
                phase=ph if ph else None,
            )
        )
    return descs


def _dc_current_desc() -> SolarEdgeSensorEntityDescription:
    return SolarEdgeSensorEntityDescription(
        key="dc_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        icon="mdi:current-dc",
        register_key="I_DC_Current",
        scale_factor_key="I_DC_Current_SF",
        sunspec_not_impl=SunSpecNotImpl.UINT16,
        uid_suffix="dc_current",
    )


def _dc_voltage_desc() -> SolarEdgeSensorEntityDescription:
    return SolarEdgeSensorEntityDescription(
        key="dc_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        register_key="I_DC_Voltage",
        scale_factor_key="I_DC_Voltage_SF",
        sunspec_not_impl=SunSpecNotImpl.UINT16,
        uid_suffix="dc_voltage",
    )


def _dc_power_desc() -> SolarEdgeSensorEntityDescription:
    return SolarEdgeSensorEntityDescription(
        key="dc_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-power",
        register_key="I_DC_Power",
        scale_factor_key="I_DC_Power_SF",
        sunspec_not_impl=SunSpecNotImpl.INT16,
        uid_suffix="dc_power",
    )


def _heat_sink_desc() -> SolarEdgeSensorEntityDescription:
    return SolarEdgeSensorEntityDescription(
        key="temp_sink",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        register_key="I_Temp_Sink",
        scale_factor_key="I_Temp_SF",
        sunspec_not_impl=SunSpecNotImpl.INT16,
        uid_suffix="temp_sink",
        extra_not_impl_values=(0x0,),
    )


# Pre-built description lists
INVERTER_SENSOR_DESCRIPTIONS: list[SolarEdgeSensorEntityDescription] = [
    *_ac_current_descs(),
    *_voltage_descs_inverter(),
    *_ac_power_descs(with_phases=False),
    _ac_freq_desc(),
    *_phase_descs(
        "ac_va",
        "ac_va",
        "AC_VA",
        "AC_VA_SF",
        SensorDeviceClass.APPARENT_POWER,
        UnitOfApparentPower.VOLT_AMPERE,
        (None,),
        "AC Apparent Power",
    ),
    *_phase_descs(
        "ac_var",
        "ac_var",
        "AC_var",
        "AC_var_SF",
        SensorDeviceClass.REACTIVE_POWER,
        UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        (None,),
        "AC Reactive Power",
    ),
    *_phase_descs(
        "ac_pf",
        "ac_pf",
        "AC_PF",
        "AC_PF_SF",
        SensorDeviceClass.POWER_FACTOR,
        PERCENTAGE,
        (None,),
        "AC Power Factor",
    ),
    _dc_current_desc(),
    _dc_voltage_desc(),
    _dc_power_desc(),
    _heat_sink_desc(),
]

METER_SENSOR_DESCRIPTIONS: list[SolarEdgeSensorEntityDescription] = [
    *_ac_current_descs(),
    *_voltage_descs_meter(),
    _ac_freq_desc(),
    *_ac_power_descs(with_phases=True),
    *_phase_descs(
        "ac_va",
        "ac_va",
        "AC_VA",
        "AC_VA_SF",
        SensorDeviceClass.APPARENT_POWER,
        UnitOfApparentPower.VOLT_AMPERE,
        (None, "A", "B", "C"),
        "AC Apparent Power",
    ),
    *_phase_descs(
        "ac_var",
        "ac_var",
        "AC_var",
        "AC_var_SF",
        SensorDeviceClass.REACTIVE_POWER,
        UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        (None, "A", "B", "C"),
        "AC Reactive Power",
    ),
    *_phase_descs(
        "ac_pf",
        "ac_pf",
        "AC_PF",
        "AC_PF_SF",
        SensorDeviceClass.POWER_FACTOR,
        PERCENTAGE,
        (None, "A", "B", "C"),
        "AC Power Factor",
    ),
]


# ---------------------------------------------------------------------------
# Name mapping — description key -> human-readable name
# ---------------------------------------------------------------------------

_SENSOR_NAMES: dict[str, str] = {}


def _sensor_name(desc: SolarEdgeSensorEntityDescription) -> str:
    """Derive human-readable name from a description."""
    phase = desc.phase
    uid = desc.uid_suffix

    if uid == "ac_current":
        return "AC Current" if phase is None else f"AC Current {phase.upper()}"
    if uid == "ac_voltage":
        return "AC Voltage" if phase is None else f"AC Voltage {phase.upper()}"
    if uid == "ac_power":
        return "AC Power" if phase is None else f"AC Power {phase.upper()}"
    if uid == "ac_frequency":
        return "AC Frequency"
    if uid == "ac_va":
        return (
            "AC Apparent Power"
            if phase is None
            else f"AC Apparent Power {phase.upper()}"
        )
    if uid == "ac_var":
        return (
            "AC Reactive Power"
            if phase is None
            else f"AC Reactive Power {phase.upper()}"
        )
    if uid == "ac_pf":
        return (
            "AC Power Factor" if phase is None else f"AC Power Factor {phase.upper()}"
        )
    if uid == "dc_current":
        return "DC Current"
    if uid == "dc_voltage":
        return "DC Voltage"
    if uid == "dc_power":
        return "DC Power"
    if uid == "temp_sink":
        return "Temperature"
    return desc.key


# ---------------------------------------------------------------------------
# Base entity class
# ---------------------------------------------------------------------------


class SolarEdgeSensorBase(CoordinatorEntity, SensorEntity):
    should_poll = False
    _attr_has_entity_name = True

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(coordinator)

        self._platform = platform
        self._config_entry = config_entry

    def scale_factor(self, x: int, y: int):
        return x * (10**y)

    @property
    def device_info(self):
        return self._platform.device_info

    @property
    def config_entry_id(self):
        return self._config_entry.entry_id

    @property
    def config_entry_name(self):
        return self._config_entry.data["name"]

    @property
    def available(self) -> bool:
        return super().available and self._platform.online

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Generic description-driven sensor (Category A)
# ---------------------------------------------------------------------------


class SolarEdgeModbusSensor(SolarEdgeSensorBase):
    """Generic sensor driven by SolarEdgeSensorEntityDescription."""

    entity_description: SolarEdgeSensorEntityDescription

    def __init__(
        self,
        platform,
        config_entry,
        coordinator,
        description: SolarEdgeSensorEntityDescription,
    ):
        super().__init__(platform, config_entry, coordinator)
        self.entity_description = description

        # Resolve DID-based not_impl once at init
        if description.did_based_not_impl:
            did = self._platform.decoded_model["C_SunSpec_DID"]
            if did in (101, 102, 103):
                self._sunspec_not_impl = SunSpecNotImpl.UINT16
            elif did in (201, 202, 203, 204):
                self._sunspec_not_impl = SunSpecNotImpl.INT16
            else:
                raise RuntimeError(f"SolarEdgeModbusSensor C_SunSpec_DID {did}")
        else:
            self._sunspec_not_impl = description.sunspec_not_impl
        # Backward-compatible alias used by the legacy sensor tests.
        self.SUNSPEC_NOT_IMPL = self._sunspec_not_impl

        # Build the model key including phase
        phase = description.phase
        if phase is not None:
            self._model_key = f"{description.register_key}_{phase.upper()}"
        else:
            self._model_key = description.register_key

        # Build unique_id
        if phase is not None:
            self._attr_unique_id = (
                f"{self._platform.uid_base}_{description.uid_suffix}_{phase.lower()}"
            )
        else:
            self._attr_unique_id = f"{self._platform.uid_base}_{description.uid_suffix}"

    @property
    def name(self) -> str:
        return _sensor_name(self.entity_description)

    @property
    def entity_registry_enabled_default(self) -> bool:
        desc = self.entity_description
        if desc.enabled_default is not None:
            return desc.enabled_default

        # Runtime DID check for phase sensors
        if desc.enabled_dids is not None and desc.phase is not None:
            did = self._platform.decoded_model["C_SunSpec_DID"]
            return did in desc.enabled_dids

        return True

    @property
    def available(self) -> bool:
        """For DCCurrent, check not_impl in available (original had custom available)."""
        desc = self.entity_description
        if desc.uid_suffix == "dc_current" and not desc.did_based_not_impl:
            # Inverter DCCurrent had an available override
            if (
                self._platform.decoded_model[self._model_key] == self._sunspec_not_impl
                or self._platform.decoded_model[desc.scale_factor_key]
                == SunSpecNotImpl.INT16
                or self._platform.decoded_model[desc.scale_factor_key]
                not in SUNSPEC_SF_RANGE
            ):
                return False

        return super().available

    @property
    def native_value(self):
        desc = self.entity_description
        try:
            value = self._platform.decoded_model[self._model_key]
            sf_key = desc.scale_factor_key

            # Check not-implemented sentinel
            if value == self._sunspec_not_impl:
                return None

            # Check extra not_impl values (e.g. HeatSink 0x0)
            if value in desc.extra_not_impl_values:
                return None

            if sf_key is not None:
                sf = self._platform.decoded_model[sf_key]

                if sf == SunSpecNotImpl.INT16:
                    return None

                if desc.check_sf_range and sf not in SUNSPEC_SF_RANGE:
                    return None

                return self.scale_factor(value, sf)

            return value

        except TypeError:
            return None

    @property
    def suggested_display_precision(self) -> int | None:
        desc = self.entity_description
        if desc.fixed_precision is not None:
            return desc.fixed_precision

        if desc.scale_factor_key is not None:
            try:
                sf = self._platform.decoded_model[desc.scale_factor_key]
                if sf not in SUNSPEC_SF_RANGE:
                    return 1
                return abs(sf)
            except (KeyError, TypeError):
                return None

        return None


def _legacy_description(
    platform,
    uid_suffix: str,
    phase: str | None = None,
) -> SolarEdgeSensorEntityDescription:
    """Resolve the matching description for legacy wrapper classes."""

    did = platform.decoded_model["C_SunSpec_DID"]
    if did in (101, 102, 103):
        descriptions = INVERTER_SENSOR_DESCRIPTIONS
    elif did in (201, 202, 203, 204):
        descriptions = METER_SENSOR_DESCRIPTIONS
    else:
        raise RuntimeError(f"Unsupported C_SunSpec_DID {did}")

    for desc in descriptions:
        if desc.uid_suffix == uid_suffix and desc.phase == phase:
            return desc

    if phase is not None:
        for desc in descriptions:
            if desc.uid_suffix == uid_suffix:
                return replace(desc, phase=phase)

    raise RuntimeError(f"No description found for {uid_suffix=} {phase=}")


class ACCurrentSensor(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for current sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_current", phase),
        )


class VoltageSensor(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for voltage sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_voltage", phase),
        )


class ACPower(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for AC power sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_power", phase),
        )


class ACFrequency(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for frequency sensors."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_frequency"),
        )


class ACVoltAmp(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for apparent power sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_va", phase),
        )


class ACVoltAmpReactive(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for reactive power sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_var", phase),
        )


class ACPowerFactor(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for power factor sensors."""

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "ac_pf", phase),
        )


class DCCurrent(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for inverter DC current sensors."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "dc_current"),
        )


class DCVoltage(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for inverter DC voltage sensors."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "dc_voltage"),
        )


class DCPower(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for inverter DC power sensors."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "dc_power"),
        )


class HeatSinkTemperature(SolarEdgeModbusSensor):
    """Backward-compatible wrapper for inverter heat sink temperature sensors."""

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(
            platform,
            config_entry,
            coordinator,
            _legacy_description(platform, "temp_sink"),
        )


# ---------------------------------------------------------------------------
# Category B — custom classes that cannot be fully description-driven
# ---------------------------------------------------------------------------


class SolarEdgeDevice(SolarEdgeSensorBase):
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_device"

    @property
    def name(self) -> str:
        return "Device"

    @property
    def native_value(self):
        return self._platform.model

    @property
    def extra_state_attributes(self):
        attrs = {}

        try:
            if (
                float_to_hex(self._platform.decoded_common["B_RatedEnergy"])
                != hex(SunSpecNotImpl.FLOAT32)
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
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_version"

    @property
    def name(self) -> str:
        return "Version"

    @property
    def native_value(self):
        return self._platform.fw_version


class ACPowerInverted(SolarEdgeSensorBase):
    """Inverted AC power sensor for HA 2025.12 energy dashboard.

    HA defines grid power as positive=import, negative=export.
    SolarEdge meters use opposite convention. This class negates values.
    """

    device_class = SensorDeviceClass.POWER
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfPower.WATT
    icon = "mdi:solar-power"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_ac_power_inverted"

    @property
    def name(self) -> str:
        return "AC Power Inverted"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return AwesomeVersion(HA_VERSION) < AwesomeVersion("2026.2")

    @property
    def native_value(self):
        try:
            model_key = "AC_Power"
            if (
                self._platform.decoded_model[model_key] == SunSpecNotImpl.INT16
                or self._platform.decoded_model["AC_Power_SF"] == SunSpecNotImpl.INT16
            ):
                return None

            value = self.scale_factor(
                self._platform.decoded_model[model_key],
                self._platform.decoded_model["AC_Power_SF"],
            )
            return -value

        except TypeError:
            return None

    @property
    def suggested_display_precision(self):
        return abs(self._platform.decoded_model["AC_Power_SF"])


class SolarEdgeACEnergy(SolarEdgeSensorBase):
    """SolarEdge sensor for AC Energy watt-hour meters."""

    device_class = SensorDeviceClass.ENERGY
    state_class = SensorStateClass.TOTAL_INCREASING
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    suggested_display_precision = 3

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

    @property
    def icon(self) -> str:
        if self._phase is None:
            return None

        elif re.match("import", self._phase.lower()):
            return "mdi:transmission-tower-export"

        elif re.match("export", self._phase.lower()):
            return "mdi:transmission-tower-import"

        else:
            return None

    @property
    def unique_id(self) -> str:
        if self._phase is None:
            return f"{self._platform.uid_base}_ac_energy_kwh"
        else:
            return f"{self._platform.uid_base}_{self._phase.lower()}_kwh"

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
    def name(self) -> str:
        if self._phase is None:
            return "AC Energy"
        else:
            return f"AC Energy {re.sub('_', ' ', self._phase)}"

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


class SolarEdgeStatusSensor(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.ENUM
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_status"

    @property
    def name(self) -> str:
        return "Status"


class SolarEdgeInverterStatus(SolarEdgeStatusSensor):
    options = list(DEVICE_STATUS.values())

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["I_Status"] == SunSpecNotImpl.INT16:
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
    options = list(BATTERY_STATUS.values())

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
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_status_vendor"

    @property
    def name(self) -> str:
        return "Status Vendor"

    @property
    def native_value(self):
        try:
            if self._platform.decoded_model["I_Status_Vendor"] == SunSpecNotImpl.INT16:
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


class SolarEdgeGlobalPowerControlBlock(SolarEdgeSensorBase):
    @property
    def available(self) -> bool:
        return super().available and self._platform.global_power_control


class SolarEdgeRRCR(SolarEdgeGlobalPowerControlBlock):
    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_rrcr"

    @property
    def name(self) -> str:
        return "RRCR Status"

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

    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = PERCENTAGE
    suggested_display_precision = 0
    icon = "mdi:percent"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_active_power_limit"

    @property
    def name(self) -> str:
        return "Active Power Limit"

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

    state_class = SensorStateClass.MEASUREMENT
    suggested_display_precision = 1
    icon = "mdi:angle-acute"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_cosphi"

    @property
    def name(self) -> str:
        return "CosPhi"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return self._platform.global_power_control

    @property
    def native_value(self) -> float:
        try:
            if (
                float_to_hex(self._platform.decoded_model["I_CosPhi"])
                == hex(SunSpecNotImpl.FLOAT32)
                or self._platform.decoded_model["I_CosPhi"] > 1.0
                or self._platform.decoded_model["I_CosPhi"] < -1.0
            ):
                return None

            else:
                return round(self._platform.decoded_model["I_CosPhi"], 1)

        except KeyError:
            return None


class MeterEvents(SolarEdgeSensorBase):
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_meter_events"

    @property
    def name(self) -> str:
        return "Meter Events"

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
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_mmppt_events"

    @property
    def name(self) -> str:
        return "MMPPT Events"

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
    device_class = SensorDeviceClass.ENERGY
    state_class = SensorStateClass.TOTAL_INCREASING
    native_unit_of_measurement = ENERGY_VOLT_AMPERE_HOUR

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase
        self.last = None

    @property
    def icon(self) -> str:
        if self._phase is None:
            return None

        elif re.match("import", self._phase.lower()):
            return "mdi:transmission-tower-export"

        elif re.match("export", self._phase.lower()):
            return "mdi:transmission-tower-import"

        else:
            return None

    @property
    def unique_id(self) -> str:
        if self._phase is None:
            raise NotImplementedError
        else:
            return f"{self._platform.uid_base}_{self._phase.lower()}_vah"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    @property
    def name(self) -> str:
        if self._phase is None:
            raise NotImplementedError
        else:
            return f"Apparent Energy {re.sub('_', ' ', self._phase)}"

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
        return abs(self._platform.decoded_model["M_VAh_SF"])


class MetervarhIE(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.ENERGY
    state_class = SensorStateClass.TOTAL_INCREASING
    native_unit_of_measurement = ENERGY_VOLT_AMPERE_REACTIVE_HOUR

    def __init__(self, platform, config_entry, coordinator, phase: str = None):
        super().__init__(platform, config_entry, coordinator)

        self._phase = phase
        self.last = None

    @property
    def icon(self) -> str:
        if self._phase is None:
            return None

        elif re.match("import", self._phase.lower()):
            return "mdi:transmission-tower-export"

        elif re.match("export", self._phase.lower()):
            return "mdi:transmission-tower-import"

        else:
            return None

    @property
    def unique_id(self) -> str:
        if self._phase is None:
            raise NotImplementedError
        else:
            return f"{self._platform.uid_base}_{self._phase.lower()}_varh"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    @property
    def name(self) -> str:
        if self._phase is None:
            raise NotImplementedError
        else:
            return f"Reactive Energy {re.sub('_', ' ', self._phase)}"

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
        return abs(self._platform.decoded_model["M_varh_SF"])


# ---------------------------------------------------------------------------
# Battery sensors (Category B — custom logic)
# ---------------------------------------------------------------------------


class SolarEdgeBatteryAvgTemp(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.TEMPERATURE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfTemperature.CELSIUS
    suggested_display_precision = 1

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_avg_temp"

    @property
    def name(self) -> str:
        return "Average Temperature"

    @property
    def native_value(self):
        try:
            if (
                float_to_hex(self._platform.decoded_model["B_Temp_Average"])
                == hex(SunSpecNotImpl.FLOAT32)
                or self._platform.decoded_model["B_Temp_Average"] < BatteryLimit.Tmin
                or self._platform.decoded_model["B_Temp_Average"] > BatteryLimit.Tmax
            ):
                return None

            else:
                return self._platform.decoded_model["B_Temp_Average"]

        except TypeError:
            return None


class SolarEdgeBatteryMaxTemp(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.TEMPERATURE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfTemperature.CELSIUS
    suggested_display_precision = 1

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_temp"

    @property
    def name(self) -> str:
        return "Max Temperature"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    @property
    def native_value(self):
        try:
            if (
                float_to_hex(self._platform.decoded_model["B_Temp_Max"])
                == hex(SunSpecNotImpl.FLOAT32)
                or self._platform.decoded_model["B_Temp_Max"] < BatteryLimit.Tmin
                or self._platform.decoded_model["B_Temp_Max"] > BatteryLimit.Tmax
            ):
                return None

            else:
                return self._platform.decoded_model["B_Temp_Max"]

        except TypeError:
            return None


class SolarEdgeBatteryVoltage(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.VOLTAGE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfElectricPotential.VOLT
    suggested_display_precision = 2

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_dc_voltage"

    @property
    def name(self) -> str:
        return "DC Voltage"

    @property
    def native_value(self):
        try:
            if (
                float_to_hex(self._platform.decoded_model["B_DC_Voltage"])
                == hex(SunSpecNotImpl.FLOAT32)
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
    device_class = SensorDeviceClass.CURRENT
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    suggested_display_precision = 2
    icon = "mdi:current-dc"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_dc_current"

    @property
    def name(self) -> str:
        return "DC Current"

    @property
    def available(self) -> bool:
        try:
            if (
                float_to_hex(self._platform.decoded_model["B_DC_Current"])
                == hex(SunSpecNotImpl.FLOAT32)
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
    device_class = SensorDeviceClass.POWER
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfPower.WATT
    suggested_display_precision = 2
    icon = "mdi:lightning-bolt"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_dc_power"

    @property
    def name(self) -> str:
        return "DC Power"

    @property
    def native_value(self):
        try:
            if (
                float_to_hex(self._platform.decoded_model["B_DC_Power"])
                == hex(SunSpecNotImpl.FLOAT32)
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

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}_inverted"

    @property
    def name(self) -> str:
        return f"{super().name} Inverted"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return AwesomeVersion(HA_VERSION) < AwesomeVersion("2026.2")

    @property
    def native_value(self):
        value = super().native_value
        return None if value is None else -value


class SolarEdgeBatteryEnergyExport(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.ENERGY
    state_class = SensorStateClass.TOTAL_INCREASING
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    suggested_display_precision = 3
    icon = "mdi:battery-charging-20"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

        self._last = None
        self._count = 0
        self._log_once = None

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_energy_export"

    @property
    def name(self) -> str:
        return "Energy Export"

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
    device_class = SensorDeviceClass.ENERGY
    state_class = SensorStateClass.TOTAL_INCREASING
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    suggested_display_precision = 3
    icon = "mdi:battery-charging-100"

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)

        self._last = None
        self._count = 0
        self._log_once = None

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_energy_import"

    @property
    def name(self) -> str:
        return "Energy Import"

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
    device_class = SensorDeviceClass.ENERGY_STORAGE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    suggested_display_precision = 3

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_energy"

    @property
    def name(self) -> str:
        return "Maximum Energy"

    @property
    def native_value(self):
        if (
            float_to_hex(self._platform.decoded_model["B_Energy_Max"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_Energy_Max"] < 0
            or self._platform.decoded_model["B_Energy_Max"]
            > self._platform.decoded_common["B_RatedEnergy"]
        ):
            return None

        else:
            return self._platform.decoded_model["B_Energy_Max"]


class SolarEdgeBatteryPowerBase(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.POWER
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfPower.WATT
    entity_category = EntityCategory.DIAGNOSTIC
    suggested_display_precision = 0


class SolarEdgeBatteryMaxChargePower(SolarEdgeBatteryPowerBase):
    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_charge_power"

    @property
    def name(self) -> str:
        return "Max Charge Power"

    @property
    def available(self):
        if (
            float_to_hex(self._platform.decoded_model["B_MaxChargePower"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_MaxChargePower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxChargePower"]


class SolarEdgeBatteryMaxChargePeakPower(SolarEdgeBatteryPowerBase):
    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_charge_peak_power"

    @property
    def name(self) -> str:
        return "Peak Charge Power"

    @property
    def available(self):
        if (
            float_to_hex(self._platform.decoded_model["B_MaxChargePeakPower"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_MaxChargePeakPower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxChargePeakPower"]


class SolarEdgeBatteryMaxDischargePower(SolarEdgeBatteryPowerBase):
    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_discharge_power"

    @property
    def name(self) -> str:
        return "Max Discharge Power"

    @property
    def available(self):
        if (
            float_to_hex(self._platform.decoded_model["B_MaxDischargePower"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_MaxDischargePower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxDischargePower"]


class SolarEdgeBatteryMaxDischargePeakPower(SolarEdgeBatteryPowerBase):
    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_max_discharge_peak_power"

    @property
    def name(self) -> str:
        return "Peak Discharge Power"

    @property
    def available(self):
        if (
            float_to_hex(self._platform.decoded_model["B_MaxDischargePeakPower"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_MaxDischargePeakPower"] < 0
        ):
            return False

        return super().available

    @property
    def native_value(self):
        return self._platform.decoded_model["B_MaxDischargePeakPower"]


class SolarEdgeBatteryAvailableEnergy(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.ENERGY_STORAGE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    suggested_display_precision = 3

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(platform, config_entry, coordinator)
        self._log_warning = True

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_avail_energy"

    @property
    def name(self) -> str:
        return "Available Energy"

    @property
    def native_value(self):
        if (
            float_to_hex(self._platform.decoded_model["B_Energy_Available"])
            == hex(SunSpecNotImpl.FLOAT32)
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
    state_class = SensorStateClass.MEASUREMENT
    entity_category = EntityCategory.DIAGNOSTIC
    native_unit_of_measurement = PERCENTAGE
    suggested_display_precision = 0
    icon = "mdi:battery-heart-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_battery_soh"

    @property
    def name(self) -> str:
        return "State of Health"

    @property
    def native_value(self):
        if (
            float_to_hex(self._platform.decoded_model["B_SOH"])
            == hex(SunSpecNotImpl.FLOAT32)
            or self._platform.decoded_model["B_SOH"] < 0
            or self._platform.decoded_model["B_SOH"] > 100
        ):
            return None
        else:
            return self._platform.decoded_model["B_SOH"]


class SolarEdgeBatterySOE(SolarEdgeSensorBase):
    device_class = SensorDeviceClass.BATTERY
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = PERCENTAGE
    suggested_display_precision = 0

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_battery_soe"

    @property
    def name(self) -> str:
        return "State of Energy"

    @property
    def native_value(self):
        if (
            float_to_hex(self._platform.decoded_model["B_SOE"])
            == hex(SunSpecNotImpl.FLOAT32)
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

    entity_category = EntityCategory.DIAGNOSTIC
    icon = "mdi:content-save-cog-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_commit_pwr_settings"

    @property
    def name(self) -> str:
        return "Commit Power Settings"

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

    entity_category = EntityCategory.DIAGNOSTIC
    icon = "mdi:restore-alert"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_default_pwr_settings"

    @property
    def name(self) -> str:
        return "Default Power Settings"

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


# ---------------------------------------------------------------------------
# MMPPT sensors (Category B — access pattern differs)
# ---------------------------------------------------------------------------


class SolarEdgeDCCurrentMMPPT(SolarEdgeSensorBase):
    """DC Current for Synergy MMPPT units."""

    device_class = SensorDeviceClass.CURRENT
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    icon = "mdi:current-dc"

    @property
    def unique_id(self) -> str:
        return (
            f"{self._platform.inverter.uid_base}_dc_current_mmppt{self._platform.unit}"
        )

    @property
    def name(self) -> str:
        return "DC Current"

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
    def suggested_display_precision(self) -> int:
        return abs(self._platform.inverter.decoded_model["mmppt_DCA_SF"])


class SolarEdgeDCVoltageMMPPT(SolarEdgeSensorBase):
    """DC Voltage for Synergy MMPPT units."""

    device_class = SensorDeviceClass.VOLTAGE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfElectricPotential.VOLT

    @property
    def unique_id(self) -> str:
        return (
            f"{self._platform.inverter.uid_base}_dc_voltage_mmppt{self._platform.unit}"
        )

    @property
    def name(self) -> str:
        return "DC Voltage"

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
    def suggested_display_precision(self) -> int:
        return abs(self._platform.inverter.decoded_model["mmppt_DCV_SF"])


class SolarEdgeDCPowerMMPPT(SolarEdgeSensorBase):
    """DC Power for Synergy MMPPT units."""

    device_class = SensorDeviceClass.POWER
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfPower.WATT
    icon = "mdi:solar-power"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.inverter.uid_base}_dc_power_mmppt{self._platform.unit}"

    @property
    def name(self) -> str:
        return "DC Power"

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
    def suggested_display_precision(self) -> int:
        return abs(self._platform.inverter.decoded_model["mmppt_DCW_SF"])


class SolarEdgeTemperatureMMPPT(SolarEdgeSensorBase):
    """Temperature for Synergy MMPPT units."""

    device_class = SensorDeviceClass.TEMPERATURE
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = UnitOfTemperature.CELSIUS
    suggested_display_precision = 0

    @property
    def unique_id(self) -> str:
        return f"{self._platform.inverter.uid_base}_tmp_mmppt{self._platform.unit}"

    @property
    def name(self) -> str:
        return "Temperature"

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


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN][config_entry.entry_id]["hub"]
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    entities = []

    for inverter in hub.inverters:
        # Custom (Category B) inverter sensors
        entities.append(SolarEdgeDevice(inverter, config_entry, coordinator))
        entities.append(Version(inverter, config_entry, coordinator))
        entities.append(SolarEdgeInverterStatus(inverter, config_entry, coordinator))
        entities.append(StatusVendor(inverter, config_entry, coordinator))
        entities.append(SolarEdgeACEnergy(inverter, config_entry, coordinator))

        # Description-driven (Category A) inverter sensors
        for desc in INVERTER_SENSOR_DESCRIPTIONS:
            entities.append(
                SolarEdgeModbusSensor(inverter, config_entry, coordinator, desc)
            )

        if hub.option_detect_extras:
            entities.append(SolarEdgeRRCR(inverter, config_entry, coordinator))
            entities.append(
                SolarEdgeActivePowerLimit(inverter, config_entry, coordinator)
            )
            entities.append(SolarEdgeCosPhi(inverter, config_entry, coordinator))

        """ Power Control Block """
        if hub.option_detect_extras:
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
        # Custom (Category B) meter sensors
        entities.append(SolarEdgeDevice(meter, config_entry, coordinator))
        entities.append(Version(meter, config_entry, coordinator))
        entities.append(MeterEvents(meter, config_entry, coordinator))
        entities.append(ACPowerInverted(meter, config_entry, coordinator))

        # Description-driven (Category A) meter sensors
        for desc in METER_SENSOR_DESCRIPTIONS:
            entities.append(
                SolarEdgeModbusSensor(meter, config_entry, coordinator, desc)
            )

        # AC Energy phases for meters
        for phase in (
            "Exported",
            "Exported_A",
            "Exported_B",
            "Exported_C",
            "Imported",
            "Imported_A",
            "Imported_B",
            "Imported_C",
        ):
            entities.append(SolarEdgeACEnergy(meter, config_entry, coordinator, phase))

        # MeterVAhIE phases
        for phase in (
            "Exported",
            "Exported_A",
            "Exported_B",
            "Exported_C",
            "Imported",
            "Imported_A",
            "Imported_B",
            "Imported_C",
        ):
            entities.append(MeterVAhIE(meter, config_entry, coordinator, phase))

        # MetervarhIE phases
        for phase in (
            "Import_Q1",
            "Import_Q1_A",
            "Import_Q1_B",
            "Import_Q1_C",
            "Import_Q2",
            "Import_Q2_A",
            "Import_Q2_B",
            "Import_Q2_C",
            "Export_Q3",
            "Export_Q3_A",
            "Export_Q3_B",
            "Export_Q3_C",
            "Export_Q4",
            "Export_Q4_A",
            "Export_Q4_B",
            "Export_Q4_C",
        ):
            entities.append(MetervarhIE(meter, config_entry, coordinator, phase))

    for battery in hub.batteries:
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

    if entities:
        async_add_entities(entities)
