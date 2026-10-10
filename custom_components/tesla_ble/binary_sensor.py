"""Binary sensor entities: presence, charging, closures, diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import tesla_ble

from .entity import TeslaBLEEntity
from .manager import TeslaBLEManager


def _charging_raw(manager: TeslaBLEManager) -> int | None:
    return manager.charge_state.get("charging_state_raw")


def _is_charging(manager: TeslaBLEManager) -> bool | None:
    raw = _charging_raw(manager)
    return None if raw is None else tesla_ble.is_charging_tag(raw)


def _charger_connected(manager: TeslaBLEManager) -> bool | None:
    raw = _charging_raw(manager)
    return None if raw is None else tesla_ble.charger_connected_tag(raw)


def _closure(manager: TeslaBLEManager, key: str) -> bool | None:
    value = manager.vehicle_status.get("closures", {}).get(key)
    if value is None:
        return None
    return value in ("open", "ajar", "opening")


def _window(manager: TeslaBLEManager, key: str) -> bool | None:
    value = manager.closures_state.get(key)
    return None if value is None else bool(value)


@dataclass(frozen=True, kw_only=True)
class TeslaBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[TeslaBLEManager], Any]


BINARY_SENSOR_DESCRIPTIONS: tuple[TeslaBinarySensorDescription, ...] = (
    TeslaBinarySensorDescription(
        key="asleep",
        name="Asleep",
        value_fn=lambda m: m.vehicle_status.get("asleep"),
    ),
    TeslaBinarySensorDescription(
        key="charging",
        name="Charging",
        value_fn=_is_charging,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
    TeslaBinarySensorDescription(
        key="charger_connected",
        name="Charger connected",
        value_fn=_charger_connected,
        device_class=BinarySensorDeviceClass.PLUG,
    ),
    TeslaBinarySensorDescription(
        key="user_present",
        name="User present",
        value_fn=lambda m: m.vehicle_status.get("user_present"),
        device_class=BinarySensorDeviceClass.PRESENCE,
    ),
    TeslaBinarySensorDescription(
        key="door_driver_front",
        name="Door driver front",
        value_fn=lambda m: _closure(m, "front_driver_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_passenger_front",
        name="Door passenger front",
        value_fn=lambda m: _closure(m, "front_passenger_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_driver_rear",
        name="Door driver rear",
        value_fn=lambda m: _closure(m, "rear_driver_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_passenger_rear",
        name="Door passenger rear",
        value_fn=lambda m: _closure(m, "rear_passenger_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_driver_front",
        name="Window driver front",
        value_fn=lambda m: _window(m, "window_open_driver_front"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_passenger_front",
        name="Window passenger front",
        value_fn=lambda m: _window(m, "window_open_passenger_front"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_driver_rear",
        name="Window driver rear",
        value_fn=lambda m: _window(m, "window_open_driver_rear"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_passenger_rear",
        name="Window passenger rear",
        value_fn=lambda m: _window(m, "window_open_passenger_rear"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="connection",
        name="Bluetooth connection",
        value_fn=lambda m: m.connected,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities(
        TeslaBinarySensor(manager, description) for description in BINARY_SENSOR_DESCRIPTIONS
    )


class TeslaBinarySensor(TeslaBLEEntity, BinarySensorEntity):
    entity_description: TeslaBinarySensorDescription

    def __init__(self, manager: TeslaBLEManager, description: TeslaBinarySensorDescription) -> None:
        super().__init__(manager, f"binary_{description.key}")
        self.entity_description = description
        self._attr_name = description.name

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.manager)
