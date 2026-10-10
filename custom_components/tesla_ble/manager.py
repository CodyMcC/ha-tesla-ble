"""TeslaBLE manager: owns the Vehicle state machine and the BLE connection."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

import tesla_ble

from .ble import HaBleAdapter, TeslaBleTransport
from .const import (
    CONF_ADDRESS,
    CONF_CHARGING_AMPS_MAX,
    CONF_DISCONNECT_DELAY,
    CONF_INFOTAINMENT_POLL_INTERVAL_ACTIVE,
    CONF_INFOTAINMENT_POLL_INTERVAL_AWAKE,
    CONF_INFOTAINMENT_SLEEP_TIMEOUT,
    CONF_VCSEC_POLL_INTERVAL,
    CONF_VIN,
    CONNECT_RETRY_BACKOFF,
    DEFAULT_CHARGING_AMPS_MAX,
    DEFAULT_DISCONNECT_DELAY,
    DEFAULT_INFOTAINMENT_POLL_INTERVAL_ACTIVE,
    DEFAULT_INFOTAINMENT_POLL_INTERVAL_AWAKE,
    DEFAULT_INFOTAINMENT_SLEEP_TIMEOUT,
    DEFAULT_VCSEC_POLL_INTERVAL,
    DOMAIN,
    TICK_INTERVAL,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CHARGING_ACTIVE_STATES = ("Charging", "Starting")


class TeslaBLEManager:
    """Drives the C++ Vehicle state machine from the HA event loop."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.vin: str = entry.data[CONF_VIN]
        self.address: str = entry.data[CONF_ADDRESS]
        self.device_name = f"Tesla {self.vin[-6:]}"

        options = entry.options
        self.vcsec_poll_interval: int = options.get(CONF_VCSEC_POLL_INTERVAL, DEFAULT_VCSEC_POLL_INTERVAL)
        self.disconnect_delay: int = options.get(CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY)
        self.charging_amps_max: int = options.get(CONF_CHARGING_AMPS_MAX, DEFAULT_CHARGING_AMPS_MAX)

        # Observed state (plain dicts produced by the bindings)
        self.vehicle_status: dict[str, Any] = {}
        self.charge_state: dict[str, Any] = {}
        self.climate_state: dict[str, Any] = {}
        self.drive_state: dict[str, Any] = {}
        self.closures_state: dict[str, Any] = {}
        self.tire_pressure_state: dict[str, Any] = {}
        self.last_command: str | None = None
        self.connected = False

        self._listeners: list[Callable[[], None]] = []
        self._pending_commands = 0
        self._last_activity = 0.0
        self._connect_blocked_until = 0.0
        self._connect_lock = asyncio.Lock()
        self._tick_cancel: Callable[[], None] | None = None
        self._next_vcsec_poll = 0.0

        from .storage import TeslaStore

        self._store = TeslaStore(hass, entry.entry_id)
        self._transport = TeslaBleTransport(hass, self.address, self.device_name, self._handle_disconnect)
        self._ble_adapter = HaBleAdapter(self._transport)
        # Created in async_setup() after the store is loaded, so the C++
        # Vehicle sees the persisted private key and sessions.
        self._vehicle: tesla_ble.Vehicle | None = None

        self._poll_policy = tesla_ble.InfotainmentPollPolicy()
        self._poll_policy.set_awake_interval_ms(
            options.get(CONF_INFOTAINMENT_POLL_INTERVAL_AWAKE, DEFAULT_INFOTAINMENT_POLL_INTERVAL_AWAKE) * 1000
        )
        self._poll_policy.set_active_interval_ms(
            options.get(CONF_INFOTAINMENT_POLL_INTERVAL_ACTIVE, DEFAULT_INFOTAINMENT_POLL_INTERVAL_ACTIVE) * 1000
        )
        self._poll_policy.set_sleep_timeout_ms(
            options.get(CONF_INFOTAINMENT_SLEEP_TIMEOUT, DEFAULT_INFOTAINMENT_SLEEP_TIMEOUT) * 1000
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        await self._store.async_load()
        self._vehicle = tesla_ble.Vehicle(self._ble_adapter, self._store)
        self._vehicle.set_vin(self.vin)
        self._vehicle.set_vehicle_status_callback(self._on_vehicle_status)
        self._vehicle.set_charge_state_callback(self._on_charge_state)
        self._vehicle.set_climate_state_callback(self._on_climate_state)
        self._vehicle.set_drive_state_callback(self._on_drive_state)
        self._vehicle.set_tire_pressure_state_callback(self._on_tire_pressure_state)
        self._vehicle.set_closures_state_callback(self._on_closures_state)
        self._transport.set_notify_callback(self._on_notify)
        self._tick_cancel = async_track_time_interval(self.hass, self._async_tick, TICK_INTERVAL)
        # First connection/poll shortly after setup.
        self._next_vcsec_poll = time.monotonic()

    async def async_shutdown(self) -> None:
        if self._tick_cancel is not None:
            self._tick_cancel()
            self._tick_cancel = None
        await self._async_disconnect()
        await self._store.async_flush()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def available(self) -> bool:
        """Entities stay available once any state has been received."""
        return bool(self.vehicle_status or self.charge_state or self.connected)

    # ------------------------------------------------------------------
    # C++ callbacks (event loop thread)
    # ------------------------------------------------------------------

    def _on_vehicle_status(self, status: dict[str, Any]) -> None:
        self.vehicle_status = status
        self._notify()

    def _on_charge_state(self, state: dict[str, Any]) -> None:
        self.charge_state = state
        self._notify()

    def _on_climate_state(self, state: dict[str, Any]) -> None:
        self.climate_state = state
        self._notify()

    def _on_drive_state(self, state: dict[str, Any]) -> None:
        self.drive_state = state
        self._notify()

    def _on_tire_pressure_state(self, state: dict[str, Any]) -> None:
        self.tire_pressure_state = state
        self._notify()

    def _on_closures_state(self, state: dict[str, Any]) -> None:
        self.closures_state = state
        self._notify()

    def _on_notify(self, data: bytes) -> None:
        self._last_activity = time.monotonic()
        self._vehicle.on_rx_data(data)

    def _handle_disconnect(self) -> None:
        self.connected = False
        if self._vehicle is not None:
            self._vehicle.set_connected(False)
        self._notify()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def async_ensure_connected(self) -> None:
        """Connect if needed; raises HomeAssistantError when unreachable."""
        if self._transport.is_connected:
            return
        async with self._connect_lock:
            if self._transport.is_connected:
                return
            if time.monotonic() < self._connect_blocked_until:
                raise HomeAssistantError(
                    f"Tesla {self.vin[-6:]} is not reachable over Bluetooth; retrying soon"
                )
            try:
                await self._transport.connect()
            except Exception as err:  # noqa: BLE001 - surface any connect failure
                self._connect_blocked_until = time.monotonic() + CONNECT_RETRY_BACKOFF.total_seconds()
                raise HomeAssistantError(f"Could not connect to Tesla {self.vin[-6:]}: {err}") from err
            self._vehicle.set_connected(True)
            self.connected = True
            self._last_activity = time.monotonic()
            self._connect_blocked_until = 0.0
            # Refresh state right away.
            self._next_vcsec_poll = 0.0
            self._notify()

    async def _async_disconnect(self) -> None:
        if self.connected or self._transport.is_connected:
            self.connected = False
            if self._vehicle is not None:
                self._vehicle.set_connected(False)
            await self._transport.disconnect()
            self._notify()

    # ------------------------------------------------------------------
    # Periodic work
    # ------------------------------------------------------------------

    async def _async_tick(self, _now: Any) -> None:
        if self._transport.is_connected:
            self._vehicle.loop()
            await self._transport.async_flush()
        await self._async_handle_polls()
        await self._async_handle_idle_disconnect()

    def _infotainment_poll_decision(self, now_ms: int) -> dict[str, Any]:
        is_asleep = bool(self.vehicle_status.get("asleep", False))
        is_charging = self.charge_state.get("charging_state") in CHARGING_ACTIVE_STATES
        return self._poll_policy.update(now_ms, is_asleep, is_charging, False)

    async def _async_handle_polls(self) -> None:
        now = time.monotonic()
        now_ms = int(now * 1000)
        vcsec_due = now >= self._next_vcsec_poll
        decision = self._infotainment_poll_decision(now_ms)
        infotainment_due = self._poll_policy.should_poll(now_ms, decision["interval_ms"])
        if not (vcsec_due or infotainment_due):
            return

        if not self._transport.is_connected:
            try:
                await self.async_ensure_connected()
            except HomeAssistantError:
                # Back off; try again on a later tick.
                self._next_vcsec_poll = now + CONNECT_RETRY_BACKOFF.total_seconds()
                return

        if vcsec_due:
            self._vehicle.vcsec_poll()
            self._next_vcsec_poll = now + self.vcsec_poll_interval
        if infotainment_due:
            self._vehicle.infotainment_poll(decision["wake_policy"])
            self._poll_policy.on_poll(now_ms)
        await self._transport.async_flush()

    async def _async_handle_idle_disconnect(self) -> None:
        if not self._transport.is_connected or self._pending_commands > 0:
            return
        if time.monotonic() - self._last_activity >= self.disconnect_delay:
            await self._async_disconnect()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _make_result_callback(self, name: str) -> Callable[[tesla_ble.CommandResult], None]:
        def callback(result: tesla_ble.CommandResult) -> None:
            self._pending_commands = max(0, self._pending_commands - 1)
            if result.success:
                text = f"{name}: ok"
            elif result.skipped:
                text = f"{name}: skipped"
            else:
                text = f"{name}: failed ({result.error_message})"
            self.last_command = text
            _LOGGER.debug("Command result: %s", text)
            if result.success:
                # Ask for fresh state on the next tick.
                self._next_vcsec_poll = 0.0
            self._notify()

        return callback

    async def async_command(self, name: str, action: Callable[[Callable], None]) -> None:
        """Run a vehicle command, connecting first if needed."""
        await self.async_ensure_connected()
        self._last_activity = time.monotonic()
        self._pending_commands += 1
        self.last_command = f"{name}: pending"
        self._notify()
        try:
            action(self._make_result_callback(name))
        except Exception:
            self._pending_commands = max(0, self._pending_commands - 1)
            raise
        await self._transport.async_flush()

    async def async_pair(self) -> None:
        """Send the whitelist-add request; tap a key card on the console next."""
        await self.async_ensure_connected()
        self._last_activity = time.monotonic()
        self._pending_commands += 1
        self.last_command = "Pair: waiting for key card tap"
        self._notify()
        self._vehicle.pair(tesla_ble.KeysRole.OWNER, self._make_result_callback("Pair"))
        await self._transport.async_flush()

    async def async_force_update(self) -> None:
        await self.async_ensure_connected()
        self._last_activity = time.monotonic()
        self._vehicle.vcsec_poll()
        self._vehicle.charge_state_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)
        self._vehicle.climate_state_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)
        self._vehicle.drive_state_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)
        self._vehicle.closures_state_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)
        self._poll_policy.on_poll(int(time.monotonic() * 1000))
        await self._transport.async_flush()

    async def async_wake(self) -> None:
        await self.async_command("Wake", self._vehicle.wake)

    async def async_lock_doors(self, lock: bool) -> None:
        action = self._vehicle.lock if lock else self._vehicle.unlock
        await self.async_command("Lock" if lock else "Unlock", action)

    async def async_set_charging(self, enable: bool) -> None:
        await self.async_command(
            "Start Charging" if enable else "Stop Charging",
            lambda cb: self._vehicle.set_charging_state(enable, cb),
        )

    async def async_set_charging_amps(self, amps: int) -> None:
        await self.async_command("Set Charging Amps", lambda cb: self._vehicle.set_charging_amps(amps, cb))

    async def async_set_charging_limit(self, limit: int) -> None:
        await self.async_command("Set Charging Limit", lambda cb: self._vehicle.set_charging_limit(limit, cb))

    async def async_set_climate(self, enable: bool) -> None:
        await self.async_command("Climate On" if enable else "Climate Off", lambda cb: self._vehicle.set_climate(enable, cb))

    async def async_set_steering_wheel_heat(self, enable: bool) -> None:
        await self.async_command(
            "Steering Heat On" if enable else "Steering Heat Off",
            lambda cb: self._vehicle.set_steering_wheel_heat(enable, cb),
        )

    async def async_open_trunk(self) -> None:
        await self.async_command("Open Trunk", self._vehicle.open_trunk)

    async def async_close_trunk(self) -> None:
        await self.async_command("Close Trunk", self._vehicle.close_trunk)

    async def async_open_frunk(self) -> None:
        await self.async_command("Open Frunk", self._vehicle.open_frunk)

    async def async_open_charge_port(self) -> None:
        await self.async_command("Open Charge Port", self._vehicle.open_charge_port)

    async def async_close_charge_port(self) -> None:
        await self.async_command("Close Charge Port", self._vehicle.close_charge_port)

    async def async_vent_windows(self) -> None:
        await self.async_command("Vent Windows", self._vehicle.vent_windows)

    async def async_close_windows(self) -> None:
        await self.async_command("Close Windows", self._vehicle.close_windows)

    async def async_flash_lights(self) -> None:
        await self.async_command("Flash Lights", self._vehicle.flash_lights)

    async def async_honk_horn(self) -> None:
        await self.async_command("Honk Horn", self._vehicle.honk_horn)
