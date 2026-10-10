"""Credential durability and permissions using HA's real filesystem writer."""

import stat
from pathlib import Path

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import storage
from homeassistant.util.file import WriteError

from custom_components.tesla_ble.storage import TeslaStore

REAL_WRITE = storage.Store._async_write_data
REAL_LOAD = storage.Store._async_load
REAL_REMOVE = storage.Store.async_remove


async def test_credentials_are_private_atomic_and_write_failures_surface(hass, tmp_path, monkeypatch):
    with monkeypatch.context() as patches:
        patches.setattr(storage.Store, "_async_write_data", REAL_WRITE)
        patches.setattr(storage.Store, "_async_load", REAL_LOAD)
        patches.setattr(storage.Store, "async_remove", REAL_REMOVE)
        await check_credentials(hass, tmp_path, patches)


async def check_credentials(hass, tmp_path, monkeypatch):
    hass.config.config_dir = str(tmp_path)
    store = TeslaStore(hass, "credentials")
    await store.async_load()
    assert store.save("private_key", b"old key")
    await store.async_flush()
    path = Path(store._store.path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original = path.read_bytes()

    def fail_write(*args, **kwargs):
        raise WriteError("disk full")

    with monkeypatch.context() as patches:
        patches.setattr(storage, "write_utf8_file_atomic", fail_write)
        store.save("private_key", b"new key")
        with pytest.raises(HomeAssistantError, match="persist"):
            await store.async_flush()
    assert path.read_bytes() == original
    await store.async_flush()
    reloaded = TeslaStore(hass, "credentials")
    await reloaded.async_load()
    assert reloaded.load("private_key") == b"new key"
    await store.async_remove()
    assert not path.exists()
