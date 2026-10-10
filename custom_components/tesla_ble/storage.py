"""Persistent storage for keys and sessions, backed by Home Assistant's Store."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

import tesla_ble

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class TeslaStore(tesla_ble.StorageAdapter):
    """StorageAdapter over HA's Store (base64 in JSON).

    The C++ side calls load/save/remove synchronously from the event loop
    thread; writes update an in-memory dict immediately and persist in the
    background.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        super().__init__()
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._hass = hass
        self._data: dict[str, str] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    async def async_flush(self) -> None:
        await self._store.async_save(self._data)

    def load(self, key: str) -> bytes | None:
        value = self._data.get(key)
        if value is None:
            return None
        try:
            return base64.b64decode(value)
        except ValueError:
            _LOGGER.warning("Corrupt stored value for %s, ignoring", key)
            return None

    def save(self, key: str, data: bytes) -> bool:
        self._data[key] = base64.b64encode(data).decode("ascii")
        self._schedule_save()
        return True

    def remove(self, key: str) -> bool:
        existed = self._data.pop(key, None) is not None
        if existed:
            self._schedule_save()
        return existed

    def _schedule_save(self) -> None:
        self._hass.async_create_task(self._store.async_save(self._data))
