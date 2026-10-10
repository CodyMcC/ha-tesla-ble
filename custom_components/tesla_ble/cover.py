"""Cover entities: trunk, frunk, charge port door and windows."""

from __future__ import annotations

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import TeslaBLEEntity
from .manager import TeslaBLEManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities(
        [
            TeslaTrunkCover(manager),
            TeslaFrunkCover(manager),
            TeslaChargePortCover(manager),
            TeslaWindowsCover(manager),
        ]
    )


def _closure_open(manager: TeslaBLEManager, key: str) -> bool | None:
    value = manager.vehicle_status.get("closures", {}).get(key)
    if value is None:
        return None
    return value in ("open", "ajar", "opening")


class TeslaTrunkCover(TeslaBLEEntity, CoverEntity):
    _attr_translation_key = "trunk"
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "cover_trunk")

    @property
    def is_closed(self) -> bool | None:
        value = _closure_open(self.manager, "rear_trunk")
        return None if value is None else not value

    async def async_open_cover(self, **kwargs) -> None:
        await self.manager.async_open_trunk()

    async def async_close_cover(self, **kwargs) -> None:
        await self.manager.async_close_trunk()


class TeslaFrunkCover(TeslaBLEEntity, CoverEntity):
    _attr_translation_key = "frunk"
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_supported_features = CoverEntityFeature.OPEN

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "cover_frunk")

    @property
    def is_closed(self) -> bool | None:
        value = _closure_open(self.manager, "front_trunk")
        return None if value is None else not value

    async def async_open_cover(self, **kwargs) -> None:
        await self.manager.async_open_frunk()


class TeslaChargePortCover(TeslaBLEEntity, CoverEntity):
    _attr_translation_key = "charge_port"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "cover_charge_port")

    @property
    def is_closed(self) -> bool | None:
        open_door = self.manager.charge_state.get("charge_port_door_open")
        if open_door is not None:
            return not open_door
        return None

    async def async_open_cover(self, **kwargs) -> None:
        await self.manager.async_open_charge_port()

    async def async_close_cover(self, **kwargs) -> None:
        await self.manager.async_close_charge_port()


class TeslaWindowsCover(TeslaBLEEntity, CoverEntity):
    _attr_translation_key = "windows"
    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "cover_windows")

    @property
    def is_closed(self) -> bool | None:
        values = [
            self.manager.closures_state.get(key)
            for key in (
                "window_open_driver_front",
                "window_open_passenger_front",
                "window_open_driver_rear",
                "window_open_passenger_rear",
            )
        ]
        known = [value for value in values if value is not None]
        if not known:
            return None
        return not any(known)

    async def async_open_cover(self, **kwargs) -> None:
        await self.manager.async_vent_windows()

    async def async_close_cover(self, **kwargs) -> None:
        await self.manager.async_close_windows()
