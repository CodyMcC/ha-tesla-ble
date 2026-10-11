"""Tesla BLE integration: control a Tesla vehicle over Bluetooth LE.

Uses the TeslaBLE C++ library through its Python bindings and Home
Assistant's bluetooth stack, so local adapters and ESPHome Bluetooth
proxies both work.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import TeslaBLEConfigEntry
from .manager import TeslaBLEManager

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "device_tracker",
    "lock",
    "number",
    "sensor",
    "switch",
]


async def async_setup_entry(hass: HomeAssistant, entry: TeslaBLEConfigEntry) -> bool:
    manager = TeslaBLEManager(hass, entry)
    try:
        await manager.async_setup()
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = manager
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await manager.async_shutdown()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TeslaBLEConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TeslaBLEConfigEntry) -> None:
    from .storage import TeslaStore

    await TeslaStore(hass, entry.entry_id).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: TeslaBLEConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
