"""Shared entity base for the Tesla BLE integration."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .manager import TeslaBLEManager


def async_add_supported_entities(
    manager: TeslaBLEManager,
    async_add_entities: AddEntitiesCallback,
    entities: list[tuple[Entity, tuple[str, ...]]],
) -> None:
    def add_observed() -> None:
        ready = [(entity, fields) for entity, fields in entities if manager.capabilities.intersection(fields)]
        if ready:
            registry = er.async_get(manager.hass)
            for entity, _ in ready:
                domain = entity.unique_id.split("_", 2)[1]
                entity_id = registry.async_get_entity_id(domain, DOMAIN, entity.unique_id)
                if entity_id and registry.async_get(entity_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION:
                    registry.async_update_entity(entity_id, disabled_by=None)
            async_add_entities([entity for entity, _ in ready])
            for item in ready:
                entities.remove(item)

    manager.entry.async_on_unload(manager.async_add_listener(add_observed))
    add_observed()


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
