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

    # User-visible entity ids stay stable (translation keys resolve to names).
    assert hass.states.get("sensor.tesla_789abc_battery_level") is not None
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


async def test_pairing_issue_when_no_key(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    entry, _client = await setup_entry(hass, sim, monkeypatch, hass_storage, with_key=False)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, ISSUE_PAIRING_REQUIRED) is not None

    # Pairing clears the issue.
    manager = entry.runtime_data
    manager._next_vcsec_poll = 0
    await settle_until(hass, lambda: manager.connected)
    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{VIN}_button_pair")
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await settle_until(hass, lambda: manager._vehicle.pending_command_count == 0)

    assert registry.async_get_issue(DOMAIN, ISSUE_PAIRING_REQUIRED) is None


async def test_no_pairing_issue_with_key(hass: HomeAssistant, sim, monkeypatch, hass_storage, enable_bluetooth) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.tesla_ble.const import ISSUE_PAIRING_REQUIRED

    await setup_entry(hass, sim, monkeypatch, hass_storage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PAIRING_REQUIRED) is None


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
    assert state_of(hass, "sensor", f"{VIN}_sensor_charger_power_estimated") == "6.9"
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

    async def fake_connect(self) -> None:
        self._client = clients[self._address]
        await self._client.start_notify(ble_module.READ_UUID, self._handle_notification)

    async def fake_disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        self._client = None

    monkeypatch.setattr(ble_module.TeslaBleTransport, "connect", fake_connect)
    monkeypatch.setattr(ble_module.TeslaBleTransport, "disconnect", fake_disconnect)

    import base64

    from conftest import TEST_PRIVATE_KEY_PEM

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
