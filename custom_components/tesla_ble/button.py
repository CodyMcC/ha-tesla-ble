"""Button entities: wake, pair, refresh, and remote actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
            TeslaWakeButton(manager),
            TeslaPairButton(manager),
            TeslaForceUpdateButton(manager),
            TeslaFlashLightsButton(manager),
            TeslaHonkHornButton(manager),
        ]
    )


class TeslaWakeButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "wake"
    _attr_icon = "mdi:power"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_wake")

    async def async_press(self) -> None:
        await self.manager.async_wake()


class TeslaPairButton(TeslaBLEEntity, ButtonEntity):
    """Start pairing: press this, then tap a key card on the console."""

    _attr_translation_key = "pair"
    _attr_icon = "mdi:key-plus"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_pair")

    async def async_press(self) -> None:
        await self.manager.async_pair()


class TeslaForceUpdateButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "force_update"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_force_update")

    async def async_press(self) -> None:
        await self.manager.async_force_update()


class TeslaFlashLightsButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "flash_lights"
    _attr_icon = "mdi:car-light-high"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_flash_lights")

    async def async_press(self) -> None:
        await self.manager.async_flash_lights()


class TeslaHonkHornButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "honk_horn"
    _attr_icon = "mdi:bullhorn"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_honk_horn")

    async def async_press(self) -> None:
        await self.manager.async_honk_horn()
