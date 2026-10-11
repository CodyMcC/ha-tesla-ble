"""Presence tracker: within Bluetooth range (home) or not (away).

Bluetooth range is the reliable presence signal for a car: adverts are
observed whenever the vehicle is nearby, regardless of whether the GATT
connection is idle. Automations should use this tracker or the connectivity
binary sensor rather than entity availability, which is a data-quality
signal.
"""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TeslaBLEConfigEntry
from .entity import TeslaBLEEntity
from .manager import TeslaBLEManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities([TeslaPresenceTracker(manager)])


class TeslaPresenceTracker(TeslaBLEEntity, TrackerEntity):
    """Home while the vehicle answers over Bluetooth, away otherwise."""

    _attr_translation_key = "vehicle"
    _attr_source_type = SourceType.BLUETOOTH_LE

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "tracker", None)

    @property
    def location_name(self) -> str | None:
        return "home" if self.manager.reachable else "not_home"
