"""Switch entities: charging and steering wheel heater."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import TeslaBLEEntity
from .manager import CHARGING_ACTIVE_STATES, TeslaBLEManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities(
        [
            TeslaChargingSwitch(manager),
            TeslaSteeringWheelHeatSwitch(manager),
        ]
    )


class TeslaChargingSwitch(TeslaBLEEntity, SwitchEntity):
    _attr_name = "Charging"
    _attr_icon = "mdi:ev-station"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "switch_charging")

    @property
    def is_on(self) -> bool | None:
        state = self.manager.charge_state.get("charging_state")
        if state is None:
            return None
        return state in CHARGING_ACTIVE_STATES

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_charging(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_charging(False)


class TeslaSteeringWheelHeatSwitch(TeslaBLEEntity, SwitchEntity):
    _attr_name = "Steering wheel heater"
    _attr_icon = "mdi:steering"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "switch_steering_wheel_heat")

    @property
    def is_on(self) -> bool | None:
        return self.manager.climate_state.get("steering_wheel_heater")

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_steering_wheel_heat(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_steering_wheel_heat(False)
