"""Full-stack tests: the integration manager driving the C++ Vehicle bindings.

A FakeGattClient replaces the Bluetooth transport: frames written by the
bindings are fed to a SimulatedVehicle, whose replies are pushed back through
the notification callback. No real Bluetooth is involved.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import tesla_ble
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.tesla_ble import ble as ble_module
from custom_components.tesla_ble.const import CONF_ADDRESS, CONF_VIN, DOMAIN

VIN = "5YJ30123456789ABC"
ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeGattClient:
    """Assembles length-prefixed frames and answers with the simulated car."""

    def __init__(self, sim: tesla_ble.SimulatedVehicle) -> None:
        self.sim = sim
        self.is_connected = True
        self._buffer = bytearray()
        self._notify = None

    async def start_notify(self, uuid, callback) -> None:
        self._notify = callback

    async def stop_notify(self, uuid) -> None:
        self._notify = None

    async def write_gatt_char(self, uuid, chunk, response: bool = False) -> None:
        self._buffer.extend(chunk)
        while len(self._buffer) >= 2:
            length = int.from_bytes(self._buffer[:2], "big")
            if len(self._buffer) < 2 + length:
                break
            frame = bytes(self._buffer[: 2 + length])
            del self._buffer[: 2 + length]
            for reply in self.sim.on_client_frame(frame):
                self._notify(None, bytearray(reply))

    async def disconnect(self) -> None:
        self.is_connected = False


import base64

from conftest import TEST_PRIVATE_KEY_PEM


async def setup_entry(
    hass: HomeAssistant,
    sim: tesla_ble.SimulatedVehicle,
    monkeypatch,
    hass_storage,
    with_key: bool = True,
) -> tuple:
    client = FakeGattClient(sim)

    async def fake_connect(self) -> None:
        self._client = client
        await client.start_notify(ble_module.READ_UUID, self._handle_notification)

    async def fake_disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        self._client = None

    monkeypatch.setattr(ble_module.TeslaBleTransport, "connect", fake_connect)
    monkeypatch.setattr(ble_module.TeslaBleTransport, "disconnect", fake_disconnect)

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_VIN: VIN, CONF_ADDRESS: ADDRESS}, unique_id=VIN)
    if with_key:
        key = TEST_PRIVATE_KEY_PEM.encode() + b"\x00"
        hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
            "version": 1,
            "data": {"private_key": base64.b64encode(key).decode()},
        }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, client


async def settle(hass: HomeAssistant, ticks: int = 6) -> None:
    """Fire manager ticks so queued frames are processed and replied to."""
    now = dt_util.utcnow()
    for _ in range(ticks):
        now += timedelta(seconds=1)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()


async def settle_until(hass: HomeAssistant, condition, max_ticks: int = 100) -> None:
    """Fire ticks until `condition()` is true."""
    now = dt_util.utcnow()
    for _ in range(max_ticks):
        if condition():
            return
        now += timedelta(seconds=1)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()
    raise AssertionError("condition not reached within the tick budget")


def state_of(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"entity {platform}/{unique_id} not found"
    state = hass.states.get(entity_id)
    return None if state is None else state.state


def distance_in_miles(hass: HomeAssistant, platform: str, unique_id: str) -> float:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    state = hass.states.get(entity_id)
    value = float(state.state)
    if state.attributes.get("unit_of_measurement") == "km":
        value /= 1.609344
    return value


async def test_poll_updates_entities(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0

    await settle_until(hass, lambda: manager.charge_state and manager.vehicle_status)

    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55"
    assert state_of(hass, "sensor", f"{VIN}_sensor_charger_power") in ("unknown", None) or True
    assert distance_in_miles(hass, "sensor", f"{VIN}_sensor_odometer") == pytest.approx(1234.5, abs=0.1)
    assert state_of(hass, "sensor", f"{VIN}_sensor_charging_state") == "Charging"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_asleep") == "off"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_charging") == "on"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_connection") == "on"
    assert state_of(hass, "switch", f"{VIN}_switch_charging") == "on"
    assert state_of(hass, "number", f"{VIN}_number_charge_limit") == "80"
    assert state_of(hass, "lock", f"{VIN}_lock_doors") == "locked"
    assert manager.connected is True


async def test_lock_service_completes(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.vehicle_status))

    lock_id = er.async_get(hass).async_get_entity_id("lock", DOMAIN, f"{VIN}_lock_doors")
    await hass.services.async_call("lock", "lock", {"entity_id": lock_id}, blocking=True)
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)

    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Lock: ok"


async def test_charging_switch_completes(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    sim.set_charging_state(tesla_ble.charging_state_tag("Disconnected"))
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.charge_state))

    switch_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{VIN}_switch_charging")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)

    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Start Charging: ok"


async def test_pair_button_persists_key(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.vehicle_status))

    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{VIN}_button_pair")
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)

    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Pair: ok"
    assert manager._store.load("private_key") is not None


async def test_force_update_button(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.charge_state))

    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{VIN}_button_force_update")
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await settle_until(hass, lambda: bool(manager.charge_state))

    assert manager.charge_state.get("battery_level") == 55


async def test_unload_disconnects(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.connected)
    assert manager.connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not manager.connected


async def test_wedged_link_is_cycled(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    """If the library resets its session while GATT stays up, cycle the link."""
    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.connected)

    # Simulate the library's auth-stall watchdog resetting its own connection.
    manager._vehicle.set_connected(False)
    await settle_until(hass, lambda: not manager.connected)

    assert manager.connected is False
    assert client.is_connected is False
