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
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import tesla_ble

from .const import TeslaBLEConfigEntry
from .entity import TeslaBLEEntity
from .manager import TeslaBLEManager

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
IEC61851_OPTIONS = ["A", "B", "C", "E", "F", "Unknown"]
CHARGE_LIMIT_REASON_OPTIONS = ["None", "EVSE", "BattTempLow", "HighSoc", "Cabin", "Unknown"]


def _odometer_miles(manager: TeslaBLEManager) -> float | None:
    value = manager.drive_state.get("odometer_in_hundredths_of_a_mile")
    return None if value is None else value / 100.0


def _minutes_to_full(manager: TeslaBLEManager) -> int | None:
    return manager.charge_state.get("minutes_to_full_charge")


def _estimated_power(manager: TeslaBLEManager) -> float | None:
    voltage = manager.charge_state.get("charger_voltage")
    current = manager.charge_state.get("charger_actual_current")
    if voltage is None or current is None:
        return None
    phases = manager.charge_state.get("charger_phases") or 1
    return round(voltage * current * phases / 1000.0, 2)


def _iec61851_state(manager: TeslaBLEManager) -> str | None:
    raw = manager.charge_state.get("charging_state_raw")
    return None if raw is None else tesla_ble.iec61851_state_text(raw)


@dataclass(frozen=True, kw_only=True)
class TeslaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TeslaBLEManager], Any]
    state: str | None = "charge_state"


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
        key="evse_max_current",
        value_fn=lambda m: m.charge_state.get("charger_pilot_current"),
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="vehicle_max_charge_current",
        value_fn=lambda m: m.charge_state.get("charge_current_request_max"),
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charge_current_request",
        value_fn=lambda m: m.charge_state.get("charge_current_request"),
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    TeslaSensorDescription(
        key="charger_phases",
        value_fn=lambda m: m.charge_state.get("charger_phases"),
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="charger_power_estimated",
        value_fn=_estimated_power,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="charging_rate",
        value_fn=lambda m: m.charge_state.get("charge_rate_mph"),
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
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
        state="drive_state",
        value_fn=_odometer_miles,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILES,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    TeslaSensorDescription(
        key="outside_temp",
        state="climate_state",
        value_fn=lambda m: m.climate_state.get("outside_temp_celsius"),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="inside_temp",
        state="climate_state",
        value_fn=lambda m: m.climate_state.get("inside_temp_celsius"),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TeslaSensorDescription(
        key="tpms_front_left",
        state="tire_pressure_state",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_fl"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_front_right",
        state="tire_pressure_state",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_fr"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_rear_left",
        state="tire_pressure_state",
        value_fn=lambda m: m.tire_pressure_state.get("tpms_pressure_rl"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorDescription(
        key="tpms_rear_right",
        state="tire_pressure_state",
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
        state="drive_state",
        value_fn=lambda m: m.drive_state.get("shift_state"),
        device_class=SensorDeviceClass.ENUM,
        options=SHIFT_STATE_OPTIONS,
    ),
    TeslaSensorDescription(
        key="iec61851_state",
        value_fn=_iec61851_state,
        device_class=SensorDeviceClass.ENUM,
        options=IEC61851_OPTIONS,
    ),
    TeslaSensorDescription(
        key="charge_limit_reason",
        value_fn=lambda m: m.charge_state.get("charge_limit_reason"),
        device_class=SensorDeviceClass.ENUM,
        options=CHARGE_LIMIT_REASON_OPTIONS,
    ),
    TeslaSensorDescription(
        key="last_command",
        state=None,
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
        super().__init__(manager, f"sensor_{description.key}", description.state)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self.manager)
        if value is None and self.entity_description.device_class == SensorDeviceClass.ENUM:
            return "Unknown"
        return value
