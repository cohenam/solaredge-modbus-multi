"""Switch platform for SolarEdge Modbus Multi."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymodbus.client.mixin import ModbusClientMixin

from . import SolarEdgeConfigEntry
from .const import SunSpecNotImpl
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

    """ Power Control Options: Site Limit Control """
    for inverter in hub.inverters:
        if hub.option_site_limit_control is True:
            entities.append(
                SolarEdgeExternalProduction(inverter, config_entry, coordinator)
            )
            entities.append(
                SolarEdgeNegativeSiteLimit(inverter, config_entry, coordinator)
            )

        if hub.option_detect_extras and inverter.advanced_power_control:
            entities.append(SolarEdgeGridControl(inverter, config_entry, coordinator))

    if entities:
        async_add_entities(entities)


class SolarEdgeSwitchBase(SolarEdgeEntityBase, SwitchEntity):
    """Base class for SolarEdge switch entities."""


class SolarEdgeExternalProduction(SolarEdgeSwitchBase):
    """External Production switch. Indicates a non-SolarEdge power sorce in system."""

    entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        try:
            if self._platform.decoded_model["E_Lim_Ctl_Mode"] == SunSpecNotImpl.UINT16:
                return False

            return super().available

        except KeyError:
            return False

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_external_production"

    @property
    def name(self) -> str:
        return "External Production"

    @property
    def entity_registry_enabled_default(self) -> bool:
        return False

    @property
    def is_on(self) -> bool:
        return (int(self._platform.decoded_model["E_Lim_Ctl_Mode"]) >> 10) & 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        set_bits = int(self._platform.decoded_model["E_Lim_Ctl_Mode"])
        set_bits = set_bits | (1 << 10)

        _LOGGER.debug(f"set {self.unique_id} bits {set_bits:016b}")

        await self._platform.write_registers(
            address=57344,
            payload=ModbusClientMixin.convert_to_registers(
                set_bits,
                data_type=ModbusClientMixin.DATATYPE.UINT16,
                word_order="little",
            ),
        )
        await self.async_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        set_bits = int(self._platform.decoded_model["E_Lim_Ctl_Mode"])
        set_bits = set_bits & ~(1 << 10)

        _LOGGER.debug(f"set {self.unique_id} bits {set_bits:016b}")

        await self._platform.write_registers(
            address=57344,
            payload=ModbusClientMixin.convert_to_registers(
                set_bits,
                data_type=ModbusClientMixin.DATATYPE.UINT16,
                word_order="little",
            ),
        )
        await self.async_update()


class SolarEdgeNegativeSiteLimit(SolarEdgeSwitchBase):
    """Negative Site Limit switch. Sets minimum import power when enabled."""

    entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        try:
            if self._platform.decoded_model["E_Lim_Ctl_Mode"] == SunSpecNotImpl.UINT16:
                return False

            return super().available

        except KeyError:
            return False

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_negative_site_limit"

    @property
    def name(self) -> str:
        return "Negative Site Limit"

    @property
    def is_on(self) -> bool:
        return (int(self._platform.decoded_model["E_Lim_Ctl_Mode"]) >> 11) & 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        set_bits = int(self._platform.decoded_model["E_Lim_Ctl_Mode"])
        set_bits = set_bits | (1 << 11)

        _LOGGER.debug(f"set {self.unique_id} bits {set_bits:016b}")

        await self._platform.write_registers(
            address=57344,
            payload=ModbusClientMixin.convert_to_registers(
                set_bits,
                data_type=ModbusClientMixin.DATATYPE.UINT16,
                word_order="little",
            ),
        )
        await self.async_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        set_bits = int(self._platform.decoded_model["E_Lim_Ctl_Mode"])
        set_bits = set_bits & ~(1 << 11)

        _LOGGER.debug(f"set {self.unique_id} bits {set_bits:016b}")

        await self._platform.write_registers(
            address=57344,
            payload=ModbusClientMixin.convert_to_registers(
                set_bits,
                data_type=ModbusClientMixin.DATATYPE.UINT16,
                word_order="little",
            ),
        )
        await self.async_update()


class SolarEdgeGridControl(SolarEdgeSwitchBase):
    """Grid Control boolean switch. This is "AdvancedPwrControlEn" in specs."""

    entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._platform.advanced_power_control
            and "AdvPwrCtrlEn" in self._platform.decoded_model.keys()
        )

    @property
    def unique_id(self) -> str:
        return f"{self._platform.uid_base}_adv_pwr_ctrl"

    @property
    def name(self) -> str:
        return "Advanced Power Control"

    @property
    def is_on(self) -> bool:
        return self._platform.decoded_model["AdvPwrCtrlEn"] == 0x1

    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"set {self.unique_id} to 0x1")

        await self._platform.write_registers(
            address=61762,
            payload=ModbusClientMixin.convert_to_registers(
                0x1, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
            ),
        )
        await self.async_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"set {self.unique_id} to 0x0")

        await self._platform.write_registers(
            address=61762,
            payload=ModbusClientMixin.convert_to_registers(
                0x0, data_type=ModbusClientMixin.DATATYPE.INT32, word_order="little"
            ),
        )
        await self.async_update()
