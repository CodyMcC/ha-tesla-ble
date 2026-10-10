"""BLE transport over Home Assistant's bluetooth stack.

Works with local adapters and ESPHome Bluetooth proxies alike:
``bluetooth.async_ble_device_from_address`` returns whatever HA resolved,
and ``bleak_retry_connector`` routes the connection accordingly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

import tesla_ble

from .const import READ_UUID, WRITE_CHUNK_SIZE, WRITE_UUID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3


class TeslaBleTransport:
    """Owns the GATT connection; frames are queued and written by the manager."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        on_disconnect: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._address = address
        self._name = name
        self._on_disconnect = on_disconnect
        self._client: BleakClientWithServiceCache | None = None
        self._write_queue: list[bytes] = []
        self._notify_callback: Callable[[bytes], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def set_notify_callback(self, callback: Callable[[bytes], None]) -> None:
        self._notify_callback = callback

    async def connect(self) -> None:
        from homeassistant.components import bluetooth

        device: BLEDevice | None = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if device is None:
            raise BleakNotFoundError(f"Device {self._address} is not currently visible")

        _LOGGER.debug("Connecting to %s (%s)", self._address, device.name)
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self._name,
            max_attempts=CONNECT_ATTEMPTS,
            disconnected_callback=self._handle_disconnected,
        )
        await client.start_notify(READ_UUID, self._handle_notification)
        self._client = client
        _LOGGER.debug("Connected to %s", self._address)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._write_queue.clear()
        if client is None:
            return
        try:
            await client.stop_notify(READ_UUID)
        except Exception:  # noqa: BLE001 - best effort on a dying connection
            pass
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        _LOGGER.debug("Disconnected from %s", self._address)

    def queue_write(self, data: bytes) -> None:
        for offset in range(0, len(data), WRITE_CHUNK_SIZE):
            self._write_queue.append(data[offset : offset + WRITE_CHUNK_SIZE])

    async def async_flush(self) -> None:
        client = self._client
        if client is None:
            self._write_queue.clear()
            return
        while self._write_queue:
            chunk = self._write_queue.pop(0)
            try:
                await client.write_gatt_char(WRITE_UUID, chunk, response=False)
            except Exception:  # noqa: BLE001 - treat any write error as link loss
                _LOGGER.debug("GATT write failed; dropping connection", exc_info=True)
                self._write_queue.clear()
                self._handle_disconnected()
                return

    def _handle_notification(self, _sender: object, data: bytearray) -> None:
        if self._notify_callback is not None:
            self._notify_callback(bytes(data))

    def _handle_disconnected(self, _client: object = None) -> None:
        if self._client is not None:
            _LOGGER.debug("Disconnected from %s", self._address)
        self._client = None
        self._write_queue.clear()
        self._on_disconnect()


class HaBleAdapter(tesla_ble.BleAdapter):
    """TeslaBLE BleAdapter that hands frames to the transport's write queue."""

    def __init__(self, transport: TeslaBleTransport) -> None:
        super().__init__()
        self._transport = transport

    def connect(self, address: str) -> None:
        # Connection lifecycle is owned by the manager.
        pass

    def disconnect(self) -> None:
        pass

    def write(self, data: bytes) -> bool:
        self._transport.queue_write(bytes(data))
        return True
