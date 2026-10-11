"""Button entities: wake, pair, refresh, and remote actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
            TeslaWakeButton(manager),
            TeslaPairButton(manager),
            TeslaForceUpdateButton(manager),
            TeslaRegenerateKeyButton(manager),
            TeslaUnlatchDriverDoorButton(manager),
            TeslaFlashLightsButton(manager),
            TeslaHonkHornButton(manager),
            TeslaSoftwareUpdateInstallButton(manager),
            TeslaSoftwareUpdateCancelButton(manager),
        ]
    )


class TeslaWakeButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "wake"
    _attr_icon = "mdi:power"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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


class TeslaRegenerateKeyButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "regenerate_key"
    _attr_icon = "mdi:key-change"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_regenerate_key")

    async def async_press(self) -> None:
        await self.manager.async_regenerate_key()


class TeslaUnlatchDriverDoorButton(TeslaBLEEntity, ButtonEntity):
    _attr_translation_key = "unlatch_driver_door"
    _attr_icon = "mdi:car-door-lock-open"
    _attr_entity_registry_enabled_default = False

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_unlatch_driver_door")

    async def async_press(self) -> None:
        await self.manager.async_unlatch_driver_door()


# CarServer software update status tags.
UPDATE_STATUS_SCHEDULED = 3
UPDATE_STATUS_AVAILABLE = 4
UPDATE_STATUS_DOWNLOADING_WIFI_WAIT = 5
UPDATE_STATUS_DOWNLOADING = 6


class TeslaSoftwareUpdateInstallButton(TeslaBLEEntity, ButtonEntity):
    """Install a downloaded update; only meaningful once it is ready."""

    _attr_translation_key = "software_update_install"
    _attr_icon = "mdi:cellphone-arrow-down"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_software_update_install", "software_update_state")

    @property
    def available(self) -> bool:
        return super().available and self.manager.software_update_state.get("status") == UPDATE_STATUS_AVAILABLE

    async def async_press(self) -> None:
        await self.manager.async_install_software_update()


class TeslaSoftwareUpdateCancelButton(TeslaBLEEntity, ButtonEntity):
    """Cancel a scheduled or in-progress download."""

    _attr_translation_key = "software_update_cancel"
    _attr_icon = "mdi:close-circle-outline"

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "button_software_update_cancel", "software_update_state")

    @property
    def available(self) -> bool:
        return super().available and self.manager.software_update_state.get("status") in (
            UPDATE_STATUS_SCHEDULED,
            UPDATE_STATUS_DOWNLOADING_WIFI_WAIT,
            UPDATE_STATUS_DOWNLOADING,
        )

    async def async_press(self) -> None:
        await self.manager.async_cancel_software_update()
