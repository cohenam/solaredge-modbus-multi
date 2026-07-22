"""Component to interface with binary sensors."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolarEdgeConfigEntry
from .entity import SolarEdgeEntityBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SolarEdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = config_entry.runtime_data.hub
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    for inverter in hub.inverters:
        if hub.option_detect_extras and inverter.advanced_power_control:
            entities.append(AdvPowerControlEnabled(inverter, config_entry, coordinator))

        entities.append(GridStatusOnOff(inverter, config_entry, coordinator))

    if entities:
        async_add_entities(entities)


class SolarEdgeBinarySensorBase(SolarEdgeEntityBase, BinarySensorEntity):
    """Base class for SolarEdge binary sensor entities."""


class AdvPowerControlEnabled(SolarEdgeBinarySensorBase):
    """Grid Control boolean status. This is "AdvancedPwrControlEn" in specs."""

    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._platform.advanced_power_control is True
            and "AdvPwrCtrlEn" in self._platform.decoded_model.keys()
        )

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_adv_pwr_ctrl_en"

    @property
    def name(self) -> str:
        return "Advanced Power Control"

    @property
    def is_on(self) -> bool:
        return self._platform.decoded_model["AdvPwrCtrlEn"] == 0x1


class GridStatusOnOff(SolarEdgeBinarySensorBase):
    """Grid Status On Off. This is undocumented from discussions."""

    device_class = BinarySensorDeviceClass.POWER
    icon = "mdi:transmission-tower"

    @property
    def available(self) -> bool:
        return (
            super().available and "I_Grid_Status" in self._platform.decoded_model.keys()
        )

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_grid_status_on_off"

    @property
    def name(self) -> str:
        return "Grid Status"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return "I_Grid_Status" in self._platform.decoded_model.keys()

    @property
    def is_on(self) -> bool:
        return not self._platform.decoded_model["I_Grid_Status"]
