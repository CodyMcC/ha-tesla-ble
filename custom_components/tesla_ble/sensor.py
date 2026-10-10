"""Sensor entities: vehicle state values and text states."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import TeslaBLEEntity
from .manager import CHARGING_ACTIVE_STATES, TeslaBLEManager

CHARGING_STATE_OPTIONS = [
    "Unknown",
    "Disconnected",
    "No Power",
    "Starting",
    "Charging",
    "Complete",
    "Stopped",
    "Calibrating",
]
SHIFT_STATE_OPTIONS = ["P", "R", "N", "D", "SNA", "Invalid", "Unknown"]


def _odometer_miles(manager: TeslaBLEManager) -> float | None:
    value = manager.drive_state.get("odometer_in_hundredths_of_a_mile")
    return None if value is None else value / 100.0


def _minutes_to_full(manager: TeslaBLEManager) -> int | None:
    return manager.charge_state.get("minutes_to_full_charge")


@dataclass(frozen=True, kw_only=True)
class TeslaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TeslaBLEManager], Any]


SENSOR_DESCRIPTIONS: tuple[TeslaSensorDescription, ...] = (
    TeslaSensorDescription(
        key="battery_level",
        value_fn=lambda m: m.charge_state.get("battery_level"),
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="battery_range",
        value_fn=lambda m: m.charge_state.get("battery_range"),
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charge_energy_added",
        value_fn=lambda m: m.charge_state.get("charge_energy_added"),
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    TeslaSensorDescription(
        key="minutes_to_full_charge",
        value_fn=_minutes_to_full,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charger_power",
        value_fn=lambda m: m.charge_state.get("charger_power"),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charger_voltage",
        value_fn=lambda m: m.charge_state.get("charger_voltage"),
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charger_actual_current",
        value_fn=lambda m: m.charge_state.get("charger_actual_current"),
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charge_limit_soc",
        value_fn=lambda m: m.charge_state.get("charge_limit_soc"),
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="odometer",
        value_fn=_odometer_miles,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILES,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    TeslaSensorDescription(
        key="outside_temp",
        value_fn=lambda m: m.climate_state.get("outside_temp_celsius"),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="inside_temp",
        value_fn=lambda m: m.climate_state.get("inside_temp_celsius"),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="tpms_front_left",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_fl"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_front_right",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_fr"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_rear_left",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_rl"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_rear_right",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_rr"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="charging_state",
        value_fn=lambda m: m.charge_state.get("charging_state"),
        device_class=SensorDeviceClass.ENUM,
        options=CHARGING_STATE_OPTIONS,
    ),
    TeslaSensorDescription(
        key="shift_state",
        value_fn=lambda m: m.drive_state.get("shift_state"),
        device_class=SensorDeviceClass.ENUM,
        options=SHIFT_STATE_OPTIONS,
    ),
    TeslaSensorDescription(
        key="last_command",
        value_fn=lambda m: m.last_command,
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
        TeslaSensor(manager, description) for description in SENSOR_DESCRIPTIONS
    )


class TeslaSensor(TeslaBLEEntity, SensorEntity):
    entity_description: TeslaSensorDescription

    def __init__(self, manager: TeslaBLEManager, description: TeslaSensorDescription) -> None:
        super().__init__(manager, f"sensor_{description.key}")
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self.manager)
        if value is None and self.entity_description.device_class == SensorDeviceClass.ENUM:
            return "Unknown"
        return value
