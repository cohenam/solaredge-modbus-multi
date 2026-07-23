"""Component to interface with buttons."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymodbus.client.mixin import ModbusClientMixin

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
        entities.append(SolarEdgeRefreshButton(inverter, config_entry, coordinator))

        """ Power Control Block """
        if hub.option_detect_extras and inverter.advanced_power_control:
            entities.append(
                SolarEdgeCommitControlSettings(inverter, config_entry, coordinator)
            )
            entities.append(
                SolarEdgeDefaultControlSettings(inverter, config_entry, coordinator)
            )

    if entities:
        async_add_entities(entities)


class SolarEdgeButtonBase(SolarEdgeEntityBase, ButtonEntity):
    """Base class for SolarEdge button entities."""

    uid_suffix: str | None = None

    def __init__(self, platform, config_entry, coordinator) -> None:
        super().__init__(platform, config_entry, coordinator)
        if self.uid_suffix is not None:
            self._attr_unique_id = f"{platform.uid_base}_{self.uid_suffix}"


class SolarEdgeRefreshButton(SolarEdgeButtonBase):
    """Button to request an immediate device data update."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:refresh"
    _attr_name = "Refresh"
    uid_suffix = "refresh"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.async_update()


class SolarEdgeCommitControlSettings(SolarEdgeButtonBase):
    """Button to Commit Power Control Settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-save-cog-outline"
    _attr_name = "Commit Power Settings"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}bt_commit_pwr_settings"

    async def async_press(self) -> None:
        _LOGGER.debug(f"set {self.unique_id} to 1")

        await self._platform.write_registers(
            address=61696,
            payload=ModbusClientMixin.convert_to_registers(
                1, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
            ),
        )
        await self.async_update()


class SolarEdgeDefaultControlSettings(SolarEdgeButtonBase):
    """Button to Restore Power Control Default Settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore-alert"
    _attr_name = "Default Power Settings"

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}bt_default_pwr_settings"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    async def async_press(self) -> None:
        _LOGGER.debug(f"set {self.unique_id} to 1")

        await self._platform.write_registers(
            address=61697,
            payload=ModbusClientMixin.convert_to_registers(
                1, data_type=ModbusClientMixin.DATATYPE.UINT16, word_order="little"
            ),
        )
        await self.async_update()
