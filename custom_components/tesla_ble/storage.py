"""Persistent storage for keys and sessions, backed by Home Assistant's Store."""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

import tesla_ble

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class _DurableStore(Store):
    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except (WriteError, SerializationError) as err:
            # Store normally logs and swallows these; pairing must not proceed.
            raise HomeAssistantError("Could not persist Tesla BLE credentials") from err


class TeslaStore(tesla_ble.StorageAdapter):
    """StorageAdapter over HA's Store (base64 in JSON).

    The C++ side calls load/save/remove synchronously from the event loop
    thread; writes update an in-memory dict immediately and persist in the
    background. The manager awaits async_flush before sending a newly staged
    private key to the vehicle; synchronous save acceptance is not durability.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        super().__init__()
        self._store = _DurableStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}", private=True, atomic_writes=True)
        self._data: dict[str, str] = {}
        self._dirty = True

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._data = data if isinstance(data, dict) else {}

    async def async_flush(self) -> None:
        if not self._dirty:
            return
        data = dict(self._data)
        await self._store.async_save(data)
        self._dirty = self._data != data

    async def async_remove(self) -> None:
        await self._store.async_remove()
        self._data.clear()
        self._dirty = False

    def load(self, key: str) -> bytes | None:
        value = self._data.get(key)
        if value is None:
            return None
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            _LOGGER.warning("Corrupt stored value for %s, ignoring", key)
            return None

    def save(self, key: str, data: bytes) -> bool:
        if key in ("session_vcsec", "session_infotainment"):
            private_key = self.load("private_key")
            if private_key:
                # Vehicle persists sessions only after HMAC/status verification.
                self.save("paired_key", hashlib.sha256(private_key).hexdigest().encode())
        value = base64.b64encode(data).decode("ascii")
        if self._data.get(key) == value:
            return True
        self._data[key] = value
        self._schedule_save()
        return True

    def remove(self, key: str) -> bool:
        existed = self._data.pop(key, None) is not None
        if existed:
            self._schedule_save()
        return existed

    def _schedule_save(self) -> None:
        self._dirty = True
        self._store.async_delay_save(lambda: dict(self._data), 1)
