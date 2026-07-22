"""Shared entity base for all SolarEdge Modbus Multi platforms."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class SolarEdgeEntityBase(CoordinatorEntity):
    """Common wiring between a device object and its Home Assistant entity."""

    _attr_has_entity_name = True

    def __init__(self, platform, config_entry, coordinator):
        super().__init__(coordinator)

        self._platform = platform
        self._config_entry = config_entry

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
