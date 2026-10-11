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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import tesla_ble

from .const import TeslaBLEConfigEntry
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
    raw = manager.vehicle_status.get("closures_raw", {}).get(key)
    return None if raw is None else tesla_ble.closure_is_open(raw)


def _window(manager: TeslaBLEManager, key: str) -> bool | None:
    value = manager.closures_state.get(key)
    return None if value is None else bool(value)


DOOR_KEYS = {
    "Driver Front": "front_driver_door",
    "Driver Rear": "rear_driver_door",
    "Passenger Front": "front_passenger_door",
    "Passenger Rear": "rear_passenger_door",
}

WINDOW_KEYS = {
    "Driver Front": "window_open_driver_front",
    "Driver Rear": "window_open_driver_rear",
    "Passenger Front": "window_open_passenger_front",
    "Passenger Rear": "window_open_passenger_rear",
}


def _any(values: list[bool | None]) -> bool | None:
    return None if all(value is None for value in values) else any(values)


def _any_closure(manager: TeslaBLEManager) -> bool | None:
    return _any([_closure(manager, key) for key in DOOR_KEYS.values()])


def _any_window(manager: TeslaBLEManager) -> bool | None:
    return _any([_window(manager, key) for key in WINDOW_KEYS.values()])


def _closure_attributes(manager: TeslaBLEManager) -> dict[str, str] | None:
    values = {label: _closure(manager, key) for label, key in DOOR_KEYS.items()}
    if all(value is None for value in values.values()):
        return None
    return {label: "Open" if value else "Closed" for label, value in values.items()}


def _window_attributes(manager: TeslaBLEManager) -> dict[str, str] | None:
    values = {label: _window(manager, key) for label, key in WINDOW_KEYS.items()}
    if all(value is None for value in values.values()):
        return None
    return {label: "Open" if value else "Closed" for label, value in values.items()}


def _scheduled_charging(manager: TeslaBLEManager) -> bool | None:
    pending = manager.charge_state.get("scheduled_charging_pending")
    mode = manager.charge_state.get("scheduled_charging_mode")
    if pending is None and mode is None:
        return None
    return bool(pending) or bool(mode)


def _scheduled_departure(manager: TeslaBLEManager) -> bool | None:
    minutes = manager.charge_state.get("scheduled_departure_time_minutes")
    if minutes is None:
        return None
    return minutes > 0


@dataclass(frozen=True, kw_only=True)
class TeslaBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[TeslaBLEManager], Any]
    attrs_fn: Callable[[TeslaBLEManager], dict[str, Any] | None] | None = None
    state: str | None = "vehicle_status"


BINARY_SENSOR_DESCRIPTIONS: tuple[TeslaBinarySensorDescription, ...] = (
    TeslaBinarySensorDescription(
        key="asleep",
        value_fn=lambda m: m.vehicle_status.get("asleep"),
    ),
    TeslaBinarySensorDescription(
        key="charging",
        state="charge_state",
        value_fn=_is_charging,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
    TeslaBinarySensorDescription(
        key="charger_connected",
        state="charge_state",
        value_fn=_charger_connected,
        device_class=BinarySensorDeviceClass.PLUG,
    ),
    TeslaBinarySensorDescription(
        key="user_present",
        value_fn=lambda m: m.vehicle_status.get("user_present"),
        device_class=BinarySensorDeviceClass.PRESENCE,
    ),
    TeslaBinarySensorDescription(
        key="parking_brake",
        state="drive_state",
        value_fn=lambda m: (
            None
            if m.drive_state.get("shift_state_raw") is None
            else tesla_ble.is_parked_tag(m.drive_state["shift_state_raw"])
        ),
    ),
    TeslaBinarySensorDescription(
        key="sunroof",
        state="closures_state",
        value_fn=lambda m: (
            None
            if m.closures_state.get("sun_roof_percent_open") is None
            else m.closures_state["sun_roof_percent_open"] > 0
        ),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_driver_front",
        value_fn=lambda m: _closure(m, "front_driver_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_passenger_front",
        value_fn=lambda m: _closure(m, "front_passenger_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_driver_rear",
        value_fn=lambda m: _closure(m, "rear_driver_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="door_passenger_rear",
        value_fn=lambda m: _closure(m, "rear_passenger_door"),
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_driver_front",
        state="closures_state",
        value_fn=lambda m: _window(m, "window_open_driver_front"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_passenger_front",
        state="closures_state",
        value_fn=lambda m: _window(m, "window_open_passenger_front"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_driver_rear",
        state="closures_state",
        value_fn=lambda m: _window(m, "window_open_driver_rear"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="window_passenger_rear",
        state="closures_state",
        value_fn=lambda m: _window(m, "window_open_passenger_rear"),
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaBinarySensorDescription(
        key="doors",
        state="vehicle_status",
        value_fn=_any_closure,
        attrs_fn=_closure_attributes,
        device_class=BinarySensorDeviceClass.DOOR,
    ),
    TeslaBinarySensorDescription(
        key="windows",
        state="closures_state",
        value_fn=_any_window,
        attrs_fn=_window_attributes,
        device_class=BinarySensorDeviceClass.WINDOW,
    ),
    TeslaBinarySensorDescription(
        key="scheduled_charging",
        state="charge_state",
        value_fn=_scheduled_charging,
    ),
    TeslaBinarySensorDescription(
        key="scheduled_departure",
        state="charge_state",
        value_fn=_scheduled_departure,
    ),
    TeslaBinarySensorDescription(
        key="connection",
        state=None,
        value_fn=lambda m: m.connected,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities(
        TeslaBinarySensor(manager, description) for description in BINARY_SENSOR_DESCRIPTIONS
    )


class TeslaBinarySensor(TeslaBLEEntity, BinarySensorEntity):
    entity_description: TeslaBinarySensorDescription

    def __init__(self, manager: TeslaBLEManager, description: TeslaBinarySensorDescription) -> None:
        super().__init__(manager, f"binary_{description.key}", description.state)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.manager)
