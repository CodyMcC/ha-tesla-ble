"""Shared entity base for the Tesla BLE integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import TeslaBLEManager


class TeslaBLEEntity(Entity):
    """Base entity bound to a TeslaBLEManager."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: TeslaBLEManager, key: str, state: str | None = None) -> None:
        self.manager = manager
        self._state_key = state
        self._attr_unique_id = f"{manager.vin}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.vin)},
            name=manager.device_name,
            manufacturer="Tesla",
            model="Vehicle",
            connections={("bluetooth", manager.address)},
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.manager.state_available(self._state_key) if self._state_key else not self.manager._stopped
