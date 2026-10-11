"""Full-stack tests: the integration manager driving the C++ Vehicle bindings.

A FakeGattClient replaces the Bluetooth transport: frames written by the
bindings are fed to a SimulatedVehicle, whose replies are pushed back through
the notification callback. No real Bluetooth is involved.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import time
import weakref
from datetime import timedelta
from types import SimpleNamespace

import pytest
import tesla_ble
from conftest import TEST_PRIVATE_KEY_PEM
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.tesla_ble import ble as ble_module
from custom_components.tesla_ble.const import CONF_ADDRESS, CONF_PAIR_ON_SETUP, CONF_VIN, DOMAIN
from custom_components.tesla_ble.sensor import _software_update_attributes

VIN = "5YJ30123456789ABC"
ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeGattClient:
    """Assembles length-prefixed frames and answers with the simulated car."""

    def __init__(self, sim: tesla_ble.SimulatedVehicle) -> None:
        self.sim = sim
        self.is_connected = True
        self._buffer = bytearray()
        self._notify = None
        self._on_disconnect = None
        self.frames = []
        self.active_writes = 0
        self.max_active_writes = 0

    async def start_notify(self, uuid, callback) -> None:
        await asyncio.sleep(0)
        self._notify = callback

    async def stop_notify(self, uuid) -> None:
        self._notify = None

    async def write_gatt_char(self, uuid, chunk, response: bool = False) -> None:
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        await asyncio.sleep(0)
        self.active_writes -= 1
        self._buffer.extend(chunk)
        while len(self._buffer) >= 2:
            length = int.from_bytes(self._buffer[:2], "big")
            if len(self._buffer) < 2 + length:
                break
            frame = bytes(self._buffer[: 2 + length])
            self.frames.append(frame)
            del self._buffer[: 2 + length]
            for reply in self.sim.on_client_frame(frame):
                self._notify(None, bytearray(reply))

    async def disconnect(self) -> None:
        self.is_connected = False
        if self._on_disconnect:
            self._on_disconnect(self)


async def setup_entry(
    hass: HomeAssistant,
    sim: tesla_ble.SimulatedVehicle,
    monkeypatch,
    hass_storage,
    with_key: bool = True,
    pair_on_setup: bool = False,
    reachable: bool = True,
) -> tuple:
    client = FakeGattClient(sim)

    async def establish(_class, _device, _name, **kwargs):
        if not reachable:
            raise HomeAssistantError("test: vehicle not reachable")
        client.is_connected = True
        client._on_disconnect = kwargs["disconnected_callback"]
        return client

    from homeassistant.components import bluetooth

    monkeypatch.setattr(bluetooth, "async_ble_device_from_address", lambda *a, **kw: SimpleNamespace(address=ADDRESS, name="Tesla", rssi=-64))
    monkeypatch.setattr(bluetooth, "async_last_service_info", lambda *a, **kw: SimpleNamespace(address=ADDRESS, name="Tesla", rssi=-64, time=time.monotonic()))
    monkeypatch.setattr(ble_module, "establish_connection", establish)

    data = {CONF_VIN: VIN, CONF_ADDRESS: ADDRESS}
    if pair_on_setup:
        data[CONF_PAIR_ON_SETUP] = True
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=VIN)
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
        await hass.async_block_till_done(wait_background_tasks=True)


async def settle_until(hass: HomeAssistant, condition, max_ticks: int = 200) -> None:
    """Fire ticks until `condition()` is true."""
    now = dt_util.utcnow()
    for _ in range(max_ticks):
        if condition():
            return
        now += timedelta(seconds=1)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done(wait_background_tasks=True)
        await asyncio.sleep(0.02)
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

    await settle_until(hass, lambda: manager.charge_state and manager.vehicle_status and manager.drive_state)

    # User-visible entity ids stay stable (translation keys resolve to names).
    assert hass.states.get("sensor.tesla_789abc_battery") is not None
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55"
    assert state_of(hass, "sensor", f"{VIN}_sensor_charger_power") == "unknown"
    assert distance_in_miles(hass, "sensor", f"{VIN}_sensor_odometer") == pytest.approx(1234.5, abs=0.1)
    assert state_of(hass, "sensor", f"{VIN}_sensor_charging_state") == "Charging"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_asleep") == "off"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_charging") == "on"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_connection") == "on"
    assert state_of(hass, "switch", f"{VIN}_switch_charging") == "on"
    assert state_of(hass, "number", f"{VIN}_number_charge_limit") == "80"
    assert state_of(hass, "lock", f"{VIN}_lock_doors") == "locked"
    assert state_of(hass, "sensor", f"{VIN}_sensor_ble_signal") == "-64"
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
    assert state_of(hass, "lock", f"{VIN}_lock_doors") == "locked"

    manager._on_closures_state({"locked": True})
    manager._on_vehicle_status({"locked": False})
    assert state_of(hass, "lock", f"{VIN}_lock_doors") == "unlocked"


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
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    manager = entry.runtime_data
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
    sim.set_battery_level(61)

    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{VIN}_button_force_update")
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await settle_until(hass, lambda: manager.charge_state.get("battery_level") == 61)

    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "61"


@pytest.mark.parametrize("shutdown", ["unload", "stop"])
async def test_shutdown_disconnects_and_releases_vehicle(hass, sim, monkeypatch, hass_storage, shutdown):
    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.connected)
    assert manager.connected
    vehicle_ref = weakref.ref(manager._vehicle)

    if shutdown == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    else:
        await hass.async_stop()
    await hass.async_block_till_done()
    assert not manager.connected
    assert not client.is_connected
    gc.collect()
    assert vehicle_ref() is None


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


async def test_pairing_issue_when_no_key(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_PAIRING_REQUIRED}_{entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # Pairing clears the issue.
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.connected)
    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{VIN}_button_pair")
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)

    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_no_pairing_issue_with_key(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    await settle_until(hass, lambda: bool(entry.runtime_data.charge_state))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PAIRING_REQUIRED}_{entry.entry_id}") is None


async def test_setup_pairs_automatically(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    """The pairing step of onboarding hands off to a single automatic attempt."""
    from homeassistant.components import persistent_notification

    notifications: list[str] = []
    monkeypatch.setattr(
        persistent_notification,
        "async_create",
        lambda hass, message, title=None, notification_id=None: notifications.append(message),
    )

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False, pair_on_setup=True)
    manager = entry.runtime_data
    await settle_until(hass, lambda: manager.paired)

    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Pair: ok"
    assert any("key card" in message for message in notifications)
    assert any("paired" in message.lower() for message in notifications)


async def test_setup_pairing_reports_unreachable_vehicle(
    hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth
) -> None:
    from homeassistant.components import persistent_notification

    notifications: list[str] = []
    monkeypatch.setattr(
        persistent_notification,
        "async_create",
        lambda hass, message, title=None, notification_id=None: notifications.append(message),
    )

    entry, _client = await setup_entry(
        hass, sim, monkeypatch, hass_storage, with_key=False, pair_on_setup=True, reachable=False
    )
    manager = entry.runtime_data
    await settle_until(hass, lambda: any("could not start" in message.lower() for message in notifications))

    assert manager.paired is False
    # The attempt generated the vehicle key, so restarts no longer auto-pair.
    assert manager._vehicle.has_private_key


VIN2 = "5YJ30123456789DEF"
ADDRESS2 = "11:22:33:44:55:66"


async def test_climate_entity(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    sim.set_inside_temp(21.0)
    sim.set_climate_on(True)
    sim.set_climate_keeper_mode(4)  # Dog Mode
    sim.set_bioweapon_mode(False)

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.climate_state))

    climate_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{VIN}_climate_climate")
    assert climate_id, "climate entity not found"
    state = hass.states.get(climate_id)
    assert state.state == "heat_cool"
    assert state.attributes["preset_mode"] == "Dog Mode"
    assert state.attributes["current_temperature"] == 21.0
    assert state.attributes["fan_mode"] == "Normal"

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": climate_id, "temperature": 22.5}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Set Climate Temp: ok"

    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": climate_id, "preset_mode": "Dog Mode"}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Climate Keeper Dog: ok"

    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": climate_id, "fan_mode": "Bioweapon Mode"}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Bioweapon On: ok"

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    assert state_of(hass, "sensor", f"{VIN}_sensor_last_command") == "Climate Off: ok"

    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": climate_id, "preset_mode": "Defrost"}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    results = []
    remove = manager.async_add_listener(lambda: results.append(manager.last_command))
    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": climate_id, "preset_mode": "Normal"}, blocking=True
    )
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    remove()
    assert "Defrost Off: ok" in results
    assert manager.last_command == "Climate Keeper Off: ok"


async def test_parity_entities(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    """ESPhome parity: charging diagnostics, parking brake, sentry, latch."""
    sim.set_charger_pilot_current(16)
    sim.set_charger_phases(3)
    sim.set_charger_voltage(230)
    sim.set_charger_actual_current(10)
    sim.set_sunroof_percent(50)
    sim.set_sentry_mode_state(3)  # Armed
    sim.set_charge_port_latch(3)  # Engaged

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.charge_state and manager.closures_state)

    assert state_of(hass, "sensor", f"{VIN}_sensor_evse_max_current") == "16"
    assert state_of(hass, "sensor", f"{VIN}_sensor_charger_phases") == "3"
    assert state_of(hass, "sensor", f"{VIN}_sensor_charger_power") == "6.9"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_parking_brake") == "on"
    assert state_of(hass, "switch", f"{VIN}_switch_sentry_mode") == "on"
    assert state_of(hass, "lock", f"{VIN}_lock_charge_port_latch") == "locked"

    # Disabled-by-default entities still exist in the registry
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{VIN}_binary_sunroof")
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{VIN}_sensor_charge_current_request")
    assert registry.async_get_entity_id("button", DOMAIN, f"{VIN}_button_regenerate_key")


async def test_multiple_vehicles(hass: HomeAssistant, monkeypatch, hass_storage, enable_bluetooth) -> None:
    """Two vehicles coexist: separate connections, devices and entities."""
    sim1 = tesla_ble.SimulatedVehicle(VIN)
    sim1.set_battery_level(55)
    sim2 = tesla_ble.SimulatedVehicle(VIN2)
    sim2.set_battery_level(70)

    clients = {ADDRESS: FakeGattClient(sim1), ADDRESS2: FakeGattClient(sim2)}

    async def establish(_class, device, _name, **kwargs):
        client = clients[device.address]
        client.is_connected = True
        client._on_disconnect = kwargs["disconnected_callback"]
        return client

    from homeassistant.components import bluetooth

    monkeypatch.setattr(bluetooth, "async_ble_device_from_address", lambda _hass, address, **kw: SimpleNamespace(address=address, name="Tesla"))
    monkeypatch.setattr(ble_module, "establish_connection", establish)

    entries = []
    for vin, address in ((VIN, ADDRESS), (VIN2, ADDRESS2)):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_VIN: vin, CONF_ADDRESS: address}, unique_id=vin)
        key = TEST_PRIVATE_KEY_PEM.encode() + b"\x00"
        hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
            "version": 1,
            "data": {"private_key": base64.b64encode(key).decode()},
        }
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        entries.append(entry)

    for entry in entries:
        entry.runtime_data._next_vcsec_poll = 0

    await settle_until(
        hass,
        lambda: entries[0].runtime_data.charge_state and entries[1].runtime_data.charge_state,
    )

    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55"
    assert state_of(hass, "sensor", f"{VIN2}_sensor_battery_level") == "70"
    assert entries[0].runtime_data.connected
    assert entries[1].runtime_data.connected

    # Each vehicle has its own device in the registry
    from homeassistant.helpers import device_registry as dr

    devices = dr.async_get(hass)
    for entry, vin in ((entries[0], VIN), (entries[1], VIN2)):
        assert devices.async_get_device_by_identifier((DOMAIN, vin), entry.entry_id) is not None


async def test_slow_connect_has_one_tick_and_unload_joins_services(hass, sim, monkeypatch, hass_storage):
    from homeassistant.exceptions import HomeAssistantError

    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        client._on_disconnect = kwargs["disconnected_callback"]
        entered.set()
        await release.wait()
        return client

    monkeypatch.setattr(ble_module, "establish_connection", slow_connect)
    tick = asyncio.create_task(manager._async_tick(None))
    await entered.wait()
    await asyncio.gather(*(manager._async_tick(None) for _ in range(12)))
    service = asyncio.create_task(manager.async_wake())
    await asyncio.sleep(0)
    unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
    while not manager._stopped:
        await asyncio.sleep(0)
    second_shutdown = asyncio.create_task(manager.async_shutdown())
    await asyncio.sleep(0)
    release.set()
    assert await unload
    await second_shutdown
    await asyncio.gather(tick, service, return_exceptions=True)
    assert tick.cancelled() and service.cancelled()
    assert calls == 1 and not manager.connected and not client.is_connected
    assert manager._vehicle is None and manager._transport._notify_callback is None
    await manager._async_tick(None)
    with pytest.raises(HomeAssistantError, match="unloaded"):
        await manager.async_wake()
    assert calls == 1


async def test_monotonic_wrap_and_stale_telemetry(hass, sim, monkeypatch, hass_storage):
    from homeassistant.components import bluetooth

    from custom_components.tesla_ble import manager as manager_module

    clock = SimpleNamespace(monotonic=lambda: (2**32 + 123_000) / 1000)
    monkeypatch.setattr(manager_module, "time", clock)
    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await settle_until(hass, lambda: bool(manager.charge_state))
    await manager.async_force_update()
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)
    assert manager_module.monotonic_ms() == 123_000
    await client.disconnect()
    monkeypatch.setattr(bluetooth, "async_ble_device_from_address", lambda *a, **kw: None)
    clock.monotonic = lambda: (2**32 + 123_000) / 1000 + 200
    await manager._async_tick(None)
    # The vehicle status is stale, so the car is out of range: everything
    # expires instead of showing day-old values.
    assert state_of(hass, "lock", f"{VIN}_lock_doors") == "unavailable"
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "unavailable"
    clock.monotonic = lambda: (2**32 + 123_000) / 1000 + 1400
    await manager._async_tick(None)
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "unavailable"
    assert state_of(hass, "binary_sensor", f"{VIN}_binary_connection") == "off"
    assert state_of(hass, "button", f"{VIN}_button_pair") != "unavailable"


async def test_asleep_vehicle_keeps_last_known_values(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """Infotainment values stay visible while the car sleeps; unreachable expires them."""
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await settle_until(hass, lambda: bool(manager.charge_state))
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55"

    # The vehicle falls asleep: infotainment data stops, VCSEC keeps polling.
    manager.vehicle_status = {**manager.vehicle_status, "asleep": True}
    stale = manager._poll_policy.sleep_timeout_ms / 1000 * 2 + 60
    manager._state_received_at["charge_state"] = time.monotonic() - stale
    manager._state_received_at["vehicle_status"] = time.monotonic()
    manager._notify()

    assert manager.state_fresh("charge_state") is False
    assert manager.state_available("charge_state") is True
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55"

    # Once the vehicle status is stale as well it is unreachable: expire.
    manager._state_received_at["vehicle_status"] = time.monotonic() - 3600
    manager._notify()
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "unavailable"


async def test_presence_tracker_follows_reachability(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """The tracker is home while the vehicle is reachable, away otherwise."""
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await settle_until(hass, lambda: manager.state_fresh("vehicle_status"))
    assert state_of(hass, "device_tracker", f"{VIN}_tracker") == "home"

    # The car leaves: no status, no adverts.
    manager._state_received_at["vehicle_status"] = time.monotonic() - 3600
    manager.rssi = None
    manager._notify()
    assert state_of(hass, "device_tracker", f"{VIN}_tracker") == "not_home"


async def test_number_limits_follow_vehicle(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """Both number ranges come from the vehicle, not from a config option."""
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await settle_until(hass, lambda: bool(manager.charge_state))

    manager.charge_state = {
        **manager.charge_state,
        "charge_limit_soc": 80,
        "charge_limit_soc_min": 50,
        "charge_limit_soc_max": 90,
        "charge_current_request_max": 16,
        "charging_amps": 12,
    }
    manager._notify()

    limit = hass.states.get("number.tesla_789abc_charge_limit")
    amps = hass.states.get("number.tesla_789abc_charging_amps")
    assert limit.state == "80"
    assert limit.attributes["min"] == 50
    assert limit.attributes["max"] == 90
    assert amps.state == "12"
    assert amps.attributes["max"] == 16


async def test_fleet_style_entities_and_attributes(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """Aggregate sensors and grouped attributes mirror the fleet integration."""
    sim.set_charger_voltage(230)
    sim.set_charger_actual_current(10)
    sim.set_charger_phases(3)
    sim.set_scheduled_charging_mode(1)
    sim.set_door_open("driver_front", True)

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: bool(manager.charge_state) and bool(manager.closures_state))

    doors = hass.states.get("binary_sensor.tesla_789abc_doors")
    assert doors.state == "on"
    assert doors.attributes["Driver Front"] == "Open"
    assert doors.attributes["Driver Rear"] == "Closed"

    assert state_of(hass, "binary_sensor", f"{VIN}_binary_scheduled_charging") == "on"

    power = hass.states.get("sensor.tesla_789abc_charger_power")
    assert power.attributes["charger_volts"] == 230
    assert power.attributes["charger_phases"] == 3
    assert power.attributes["charger_amps_actual"] == 10


async def test_software_update_sensor(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """Software update state arrives via force update and maps to text."""
    sim.set_software_update_state(4)  # Available
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await manager.async_force_update()
    await settle_until(hass, lambda: bool(manager.software_update_state))
    assert state_of(hass, "sensor", f"{VIN}_sensor_software_update") == "Available"

    # No update pending (tag 1) reads as up to date, like the Fleet integration.
    sim.set_software_update_state(1)
    await manager.async_force_update()
    await settle_until(hass, lambda: manager.software_update_state.get("status") == 1)
    assert state_of(hass, "sensor", f"{VIN}_sensor_software_update") == "Up to date"


def test_software_update_release_url():
    """A known update version links to its release announcement."""
    manager = SimpleNamespace(software_update_state={"status": 4, "version": "2026.40.1 abc123"})
    attrs = _software_update_attributes(manager)
    assert attrs["version"] == "2026.40.1 abc123"
    assert attrs["release_url"] == (
        "https://www.notateslaapp.com/software-updates/version/2026.40.1/release-notes"
    )
    assert _software_update_attributes(SimpleNamespace(software_update_state={})) is None


async def test_polling_switch_pauses_and_persists(hass, sim, monkeypatch, hass_storage, enable_bluetooth):
    """The polling switch pauses polling, keeps values and survives a reload."""
    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await settle_until(hass, lambda: bool(manager.charge_state) and manager._vehicle.pending_command_count == 0)

    switch_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{VIN}_switch_polling")
    assert switch_id
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    assert manager.polling is False
    assert manager._store.load("polling") == b"0"

    await settle(hass, 3)
    assert manager._vehicle.pending_command_count == 0, "paused polling must not enqueue commands"
    assert state_of(hass, "sensor", f"{VIN}_sensor_battery_level") == "55", "paused polling keeps last values"

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.polling is False

    switch_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{VIN}_switch_polling")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    assert entry.runtime_data.polling is True
    assert entry.runtime_data._store.load("polling") == b"1"


async def test_periodic_polls_do_not_fill_queue_behind_existing_work(hass, sim, monkeypatch, hass_storage):
    from custom_components.tesla_ble import manager as manager_module

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data
    await manager.async_force_update()
    pending = manager._vehicle.pending_command_count
    assert pending > 0
    monkeypatch.setattr(manager_module, "time", SimpleNamespace(monotonic=lambda: 1_000_000.0))
    manager._next_vcsec_poll = 0
    await manager._async_tick(None)
    assert manager._vehicle.pending_command_count <= pending


@pytest.mark.parametrize("write_fails", [False, True])
async def test_pair_waits_for_durable_key_and_aborts_on_disk_error(hass, sim, monkeypatch, hass_storage, write_fails):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.storage import Store
    from homeassistant.util.file import WriteError

    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    manager = entry.runtime_data
    entered, release = asyncio.Event(), asyncio.Event()
    original = Store._async_write_data

    async def blocked_write(store, data):
        entered.set()
        await release.wait()
        if write_fails:
            raise WriteError("disk full")
        await original(store, data)

    with monkeypatch.context() as patches:
        patches.setattr(Store, "_async_write_data", blocked_write)
        pair = asyncio.create_task(manager.async_pair())
        await entered.wait()
        assert not manager.connected and not client.frames
        release.set()
        if write_fails:
            with pytest.raises(HomeAssistantError, match="persist"):
                await pair
            assert not manager.connected and not client.frames
        else:
            await pair
            assert manager.connected
            assert "private_key" in hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_pair_and_regenerated_key_stay_unpaired_on_reload(hass, sim, monkeypatch, hass_storage):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    entry, client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    manager = entry.runtime_data
    await manager.async_pair()
    await manager._async_disconnect()
    assert manager.last_command.startswith("Pair: failed") and not manager.paired
    key = manager._store.load("private_key")
    assert await hass.config_entries.async_reload(entry.entry_id)
    manager = entry.runtime_data
    assert manager._store.load("private_key") == key and not manager.paired
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PAIRING_REQUIRED}_{entry.entry_id}")
    await manager.async_regenerate_key()
    assert manager._store.load("private_key") != key and not manager.paired
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert not entry.runtime_data.paired


async def test_regeneration_does_not_acknowledge_a_failed_save(hass, sim, monkeypatch, hass_storage):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.storage import Store
    from homeassistant.util.file import WriteError

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    manager = entry.runtime_data

    async def fail_write(*args):
        raise WriteError("disk full")

    with monkeypatch.context() as patches:
        patches.setattr(Store, "_async_write_data", fail_write)
        with pytest.raises(HomeAssistantError, match="persist"):
            await manager.async_regenerate_key()
    assert manager.last_command == "Regenerate Key: failed (credentials not persisted)"
    assert not manager.paired
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_pairing_issues_are_entry_scoped_and_removal_deletes_credentials(hass, sim, monkeypatch, hass_storage):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    first, _ = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    second = MockConfigEntry(domain=DOMAIN, data={CONF_VIN: VIN2, CONF_ADDRESS: ADDRESS2}, unique_id=VIN2)
    key = TEST_PRIVATE_KEY_PEM.encode() + b"\0"
    hass_storage[f"{DOMAIN}.{second.entry_id}"] = {
        "version": 1,
        "data": {"private_key": base64.b64encode(key).decode(), "paired_key": base64.b64encode(hashlib.sha256(key).hexdigest().encode()).decode()},
    }
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_PAIRING_REQUIRED}_{first.entry_id}")
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_PAIRING_REQUIRED}_{second.entry_id}") is None
    assert await hass.config_entries.async_remove(second.entry_id)
    assert f"{DOMAIN}.{second.entry_id}" not in hass_storage
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_PAIRING_REQUIRED}_{first.entry_id}")


async def test_diagnostics_redact_identifiers(hass, sim, monkeypatch, hass_storage):
    from custom_components.tesla_ble.diagnostics import async_get_config_entry_diagnostics

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["data"]["vin"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["address"] == "**REDACTED**"
    assert diagnostics["connection"]["address"] == "**REDACTED**"
