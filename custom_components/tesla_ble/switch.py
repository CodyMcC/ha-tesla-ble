"""Switch entities: charging and steering wheel heater."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import tesla_ble

from .const import TeslaBLEConfigEntry
from .entity import TeslaBLEEntity
from .manager import CHARGING_ACTIVE_STATES, TeslaBLEManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    async_add_entities(
        [
            TeslaChargingSwitch(manager),
            TeslaSteeringWheelHeatSwitch(manager),
            TeslaSentryModeSwitch(manager),
        ]
    )


class TeslaChargingSwitch(TeslaBLEEntity, SwitchEntity):
    _attr_translation_key = "charging"
    _attr_icon = "mdi:ev-station"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "switch_charging", "charge_state")

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
    _attr_translation_key = "steering_wheel_heat"
    _attr_icon = "mdi:steering"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "switch_steering_wheel_heat", "climate_state")

    @property
    def is_on(self) -> bool | None:
        return self.manager.climate_state.get("steering_wheel_heater")

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_steering_wheel_heat(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_steering_wheel_heat(False)


class TeslaSentryModeSwitch(TeslaBLEEntity, SwitchEntity):
    _attr_translation_key = "sentry_mode"
    _attr_icon = "mdi:shield-car"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "switch_sentry_mode", "closures_state")

    @property
    def is_on(self) -> bool | None:
        raw = self.manager.closures_state.get("sentry_mode_state")
        return None if raw is None else tesla_ble.sentry_mode_active(raw)

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_sentry_mode(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_sentry_mode(False)
