"""Number entities: charge limit and charging amps."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
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
    async_add_entities(
        [
            TeslaChargingLimitNumber(manager),
            TeslaChargingAmpsNumber(manager),
        ]
    )


class TeslaChargingLimitNumber(TeslaBLEEntity, NumberEntity):
    _attr_translation_key = "charge_limit"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "number_charge_limit", "charge_state")

    @property
    def native_value(self) -> float | None:
        return self.manager.charge_state.get("charge_limit_soc")

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_charging_limit(int(value))


class TeslaChargingAmpsNumber(TeslaBLEEntity, NumberEntity):
    _attr_translation_key = "charge_amps"
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:current-ac"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "number_charge_amps", "charge_state")
        self._attr_native_max_value = manager.charging_amps_max

    @property
    def native_value(self) -> float | None:
        return self.manager.charge_state.get("charging_amps")

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_charging_amps(int(value))
