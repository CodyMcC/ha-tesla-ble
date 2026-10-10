"""Transport ownership and write ordering at the Bleak boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components import bluetooth
from homeassistant.exceptions import HomeAssistantError

from custom_components.tesla_ble import ble


class Client:
    def __init__(self):
        self.is_connected = True
        self.notify = None
        self.disconnected_callback = None
        self.disconnect_count = 0
        self.chunks = []
        self.active_writes = 0
        self.max_active_writes = 0

    async def start_notify(self, uuid, callback):
        self.notify = callback

    async def stop_notify(self, uuid):
        self.notify = None

    async def disconnect(self):
        self.disconnect_count += 1
        self.is_connected = False
        self.disconnected_callback(self)

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == ble.WRITE_UUID and response is False
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        await asyncio.sleep(0)
        self.chunks.append(bytes(data))
        self.active_writes -= 1


def transport(hass, monkeypatch, client):
    disconnected = Mock()
    result = ble.TeslaBleTransport(hass, "AA:BB:CC:DD:EE:FF", "Tesla", disconnected)
    monkeypatch.setattr(bluetooth, "async_ble_device_from_address", lambda *a, **kw: SimpleNamespace(name="Tesla"))

    async def establish(*args, **kwargs):
        client.disconnected_callback = kwargs["disconnected_callback"]
        return client

    monkeypatch.setattr(ble, "establish_connection", establish)
    return result, disconnected


async def test_concurrent_flush_preserves_chunk_order(hass, monkeypatch):
    client = Client()
    link, _ = transport(hass, monkeypatch, client)
    await link.connect()
    frame = bytes(range(100))
    link.queue_write(frame)
    await asyncio.gather(link.async_flush(), link.async_flush())
    assert b"".join(client.chunks) == frame
    assert all(len(chunk) <= 18 for chunk in client.chunks)
    assert client.max_active_writes == 1
    await link.disconnect()


@pytest.mark.parametrize("failure", ["notify", "cancel_notify", "write"])
async def test_failed_gatt_operation_disposes_client(hass, monkeypatch, failure):
    client = Client()
    link, disconnected = transport(hass, monkeypatch, client)

    async def fail(*args, **kwargs):
        if failure == "cancel_notify":
            raise asyncio.CancelledError
        raise RuntimeError("GATT failure")

    if failure != "write":
        client.start_notify = fail
        with pytest.raises(asyncio.CancelledError if failure == "cancel_notify" else RuntimeError):
            await link.connect()
    else:
        await link.connect()
        client.write_gatt_char = fail
        link.queue_write(b"frame")
        with pytest.raises(HomeAssistantError):
            await link.async_flush()
        disconnected.assert_called_once()
    assert not link.is_connected and not client.is_connected
    assert client.disconnect_count == 1
    await link.disconnect()
    assert client.disconnect_count == 1


async def test_old_client_callbacks_cannot_disrupt_new_link(hass, monkeypatch):
    old = Client()
    link, disconnected = transport(hass, monkeypatch, old)
    received = Mock()
    link.set_notify_callback(received)
    await link.connect()
    old_notify = old.notify
    await link.disconnect()
    new = Client()

    async def establish(*args, **kwargs):
        new.disconnected_callback = kwargs["disconnected_callback"]
        return new

    monkeypatch.setattr(ble, "establish_connection", establish)
    await link.connect()
    link.queue_write(b"new frame")
    old.disconnected_callback(old)
    old_notify(None, bytearray(b"stale packet"))
    assert link.is_connected
    received.assert_not_called()
    disconnected.assert_not_called()
    await link.async_flush()
    assert b"".join(new.chunks) == b"new frame"
    await link.disconnect()
