"""TeslaBLE manager: owns the Vehicle state machine and the BLE connection."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
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
    CONF_PAIR_ON_SETUP,
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
    ISSUE_PAIRING_REQUIRED,
    TICK_INTERVAL,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CHARGING_ACTIVE_STATES = ("Charging", "Starting")


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


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
        self._last_activity = 0.0
        self._connect_blocked_until = 0.0
        self._work_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._state_received_at: dict[str, float] = {}
        self.paired = False
        self._issue_id = f"{ISSUE_PAIRING_REQUIRED}_{entry.entry_id}"
        self._tick_cancel: Callable[[], None] | None = None
        self._stop_cancel: Callable[[], None] | None = None
        self._next_vcsec_poll = 0.0
        self._pairing_notification_id = f"{DOMAIN}_pairing_{entry.entry_id}"
        self._pairing_pending = False
        self._pairing_tasks: set[asyncio.Task] = set()

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
        await self._store.async_flush()
        self._create_vehicle()
        self.paired = self._vehicle.has_private_key and self._store.load("paired_key") == self._key_id()
        self._transport.set_notify_callback(self._on_notify)
        self._tick_cancel = async_track_time_interval(self.hass, self._async_tick, TICK_INTERVAL)
        self._stop_cancel = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop)
        self._next_vcsec_poll = time.monotonic()
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PAIRING_REQUIRED)
        self._async_update_pairing_issue()
        if self.entry.data.get(CONF_PAIR_ON_SETUP):
            self._consume_pair_on_setup()

    def _create_vehicle(self) -> None:
        self._vehicle = tesla_ble.Vehicle(self._ble_adapter, self._store)
        self._vehicle.set_vin(self.vin)
        self._vehicle.set_vehicle_status_callback(self._on_vehicle_status)
        self._vehicle.set_charge_state_callback(self._on_charge_state)
        self._vehicle.set_climate_state_callback(self._on_climate_state)
        self._vehicle.set_drive_state_callback(self._on_drive_state)
        self._vehicle.set_tire_pressure_state_callback(self._on_tire_pressure_state)
        self._vehicle.set_closures_state_callback(self._on_closures_state)

    def _key_id(self) -> bytes | None:
        key = self._store.load("private_key")
        return hashlib.sha256(key).hexdigest().encode() if key else None

    @asynccontextmanager
    async def _work(self) -> AsyncIterator[None]:
        if self._stopped or self.hass.is_stopping:
            raise HomeAssistantError("Tesla BLE integration is unloaded")
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            async with self._work_lock:
                if self._stopped or self.hass.is_stopping:
                    raise HomeAssistantError("Tesla BLE integration is unloaded")
                yield
        finally:
            self._tasks.discard(task)

    async def async_shutdown(self) -> None:
        self._stopped = True
        async with self._shutdown_lock:
            await self._async_shutdown()

    async def _async_shutdown(self) -> None:
        if self._stop_cancel is not None:
            self._stop_cancel()
            self._stop_cancel = None
        if self._tick_cancel is not None:
            self._tick_cancel()
            self._tick_cancel = None
        tasks = list(self._tasks - {asyncio.current_task()})
        for task in tasks:
            task.cancel()
        pairing_tasks = list(self._pairing_tasks)
        for task in pairing_tasks:
            task.cancel()
        await asyncio.gather(*tasks, *pairing_tasks, return_exceptions=True)
        try:
            await self._async_disconnect()
            await self._store.async_flush()
        finally:
            self._transport.set_notify_callback(None)
            self._clear_vehicle_callbacks()
            self._vehicle = None
            self._listeners.clear()
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            if self._pairing_pending:
                self._pairing_pending = False
                persistent_notification.async_dismiss(self.hass, self._pairing_notification_id)

    async def _async_stop(self, _event: Any) -> None:
        await self.async_shutdown()

    def _clear_vehicle_callbacks(self) -> None:
        if self._vehicle is not None:
            for state in ("vehicle_status", "charge_state", "climate_state", "drive_state", "tire_pressure_state", "closures_state"):
                getattr(self._vehicle, f"set_{state}_callback")(None)

    def _async_update_pairing_issue(self) -> None:
        """Surface a repair issue while no key is paired with the vehicle."""
        if self._vehicle is None:
            return
        if self.paired:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_PAIRING_REQUIRED,
                translation_placeholders={"vehicle": self.device_name},
            )

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def _async_notify_pairing(self, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self.device_name}: pairing",
            notification_id=self._pairing_notification_id,
        )

    def _async_notify_paired(self) -> None:
        self._pairing_pending = False
        self._async_notify_pairing(
            f"{self.device_name} is paired with Home Assistant and can now be controlled "
            "over Bluetooth."
        )

    def _consume_pair_on_setup(self) -> None:
        """Pair once right after the entry is added, when the flow requested it.

        The flag stays in the entry data; the key check makes it inert after
        the first attempt (which always generates the vehicle key first), so
        a restart never re-triggers a pairing session.
        """
        if self.paired or self._vehicle is None or self._vehicle.has_private_key or self._stopped:
            return
        task = self.hass.async_create_task(self._async_pair_on_setup())
        self._pairing_tasks.add(task)
        task.add_done_callback(self._pairing_tasks.discard)

    async def _async_pair_on_setup(self) -> None:
        try:
            await self.async_pair()
        except Exception as err:  # noqa: BLE001 - background task reports its own failure
            _LOGGER.warning("Initial pairing could not start: %s", err)
            self._pairing_pending = False
            self._async_notify_pairing(
                f"Pairing could not start: {err}. Make sure the vehicle is awake and in "
                "range of Bluetooth, then press the Pair button on the device page."
            )

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
        return not self._stopped and (self.connected or any(self.state_available(state) for state in self._state_received_at))

    def state_available(self, state: str) -> bool:
        received = self._state_received_at.get(state)
        if received is None or self._stopped:
            return False
        interval = self.vcsec_poll_interval if state == "vehicle_status" else self._poll_policy.sleep_timeout_ms / 1000
        return time.monotonic() - received <= max(120, interval * 2)

    def _received_state(self, state: str) -> None:
        self._state_received_at[state] = time.monotonic()
        self._sync_pairing()
        self._notify()

    def _sync_pairing(self) -> None:
        confirmed = self._vehicle.has_private_key and self._store.load("paired_key") == self._key_id()
        if self.paired != confirmed:
            self.paired = confirmed
            if confirmed and self._pairing_pending:
                self._async_notify_paired()
            self._async_update_pairing_issue()

    def _mark_paired(self) -> None:
        self.paired = True
        self._store.save("paired_key", self._key_id())
        self._async_update_pairing_issue()

    # ------------------------------------------------------------------
    # C++ callbacks (event loop thread)
    # ------------------------------------------------------------------

    def _on_vehicle_status(self, status: dict[str, Any]) -> None:
        self.vehicle_status = status
        self._received_state("vehicle_status")

    def _on_charge_state(self, state: dict[str, Any]) -> None:
        self.charge_state = state
        self._received_state("charge_state")

    def _on_climate_state(self, state: dict[str, Any]) -> None:
        self.climate_state = state
        self._received_state("climate_state")

    def _on_drive_state(self, state: dict[str, Any]) -> None:
        self.drive_state = state
        self._received_state("drive_state")

    def _on_tire_pressure_state(self, state: dict[str, Any]) -> None:
        self.tire_pressure_state = state
        self._received_state("tire_pressure_state")

    def _on_closures_state(self, state: dict[str, Any]) -> None:
        self.closures_state = state
        self._received_state("closures_state")

    def _on_notify(self, data: bytes) -> None:
        if self._stopped or self._vehicle is None or not self.connected:
            return
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
        async with self._work():
            await self._async_ensure_connected()

    async def _async_ensure_connected(self) -> None:
        """Connect if needed; raises HomeAssistantError when unreachable."""
        if self._stopped or self.hass.is_stopping:
            raise HomeAssistantError("Tesla BLE integration is unloaded")
        if self._transport.is_connected:
            return
        if time.monotonic() < self._connect_blocked_until:
            raise HomeAssistantError(
                f"Tesla {self.vin[-6:]} is not reachable over Bluetooth; retrying soon"
            )
        await self._store.async_flush()
        if self._stopped or self.hass.is_stopping:
            raise HomeAssistantError("Tesla BLE integration is unloaded")
        try:
            await self._transport.connect()
        except Exception as err:  # noqa: BLE001 - surface any connect failure
            self._connect_blocked_until = time.monotonic() + CONNECT_RETRY_BACKOFF.total_seconds()
            raise HomeAssistantError(f"Could not connect to Tesla {self.vin[-6:]}: {err}") from err
        if self._stopped or self.hass.is_stopping:
            await self._transport.disconnect()
            raise HomeAssistantError("Tesla BLE integration is unloaded")
        self._vehicle.set_connected(True)
        self.connected = True
        self._last_activity = time.monotonic()
        self._connect_blocked_until = 0.0
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
        if self._stopped or self.hass.is_stopping or self._work_lock.locked():
            return
        async with self._work():
            try:
                await self._async_run_tick()
            except (HomeAssistantError, ConnectionError):
                _LOGGER.debug("Vehicle unavailable during tick", exc_info=True)
            self._sync_pairing()
            self._notify()

    async def _async_run_tick(self) -> None:
        if self._transport.is_connected:
            if self._vehicle is not None and not self._vehicle.is_connected:
                # The library reset its sessions (e.g. auth stall) while the
                # GATT link is still up. Cycle the link so the next poll
                # re-authenticates on a fresh connection.
                _LOGGER.debug("Vehicle reset its session; cycling the BLE link")
                await self._async_disconnect()
            else:
                self._vehicle.loop()
                await self._transport.async_flush()
        await self._async_handle_polls()
        await self._async_handle_idle_disconnect()

    def _infotainment_poll_decision(self, now_ms: int) -> dict[str, Any]:
        is_asleep = self.state_available("vehicle_status") and bool(self.vehicle_status.get("asleep", False))
        is_charging = self.state_available("charge_state") and self.charge_state.get("charging_state") in CHARGING_ACTIVE_STATES
        is_sentry = self.state_available("closures_state") and tesla_ble.sentry_mode_active(self.closures_state.get("sentry_mode_state", 0))
        return self._poll_policy.update(now_ms, is_asleep, is_charging, is_sentry)

    async def _async_handle_polls(self) -> None:
        if self._vehicle.pending_command_count:
            return
        now = time.monotonic()
        now_ms = monotonic_ms()
        vcsec_due = now >= self._next_vcsec_poll
        decision = self._infotainment_poll_decision(now_ms)
        infotainment_due = self._poll_policy.should_poll(now_ms, decision["interval_ms"])
        if not (vcsec_due or infotainment_due):
            return

        if not self._transport.is_connected:
            try:
                await self._async_ensure_connected()
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
        if not self._transport.is_connected:
            return
        if self._vehicle is not None and self._vehicle.pending_command_count > 0:
            return
        if time.monotonic() - self._last_activity >= self.disconnect_delay:
            await self._async_disconnect()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _make_result_callback(self, name: str) -> Callable[[tesla_ble.CommandResult], None]:
        def callback(result: tesla_ble.CommandResult) -> None:
            if result.success:
                text = f"{name}: ok"
            elif result.skipped:
                text = f"{name}: skipped"
            else:
                text = f"{name}: failed ({result.error_message})"
            self.last_command = text
            _LOGGER.debug("Command result: %s", text)
            if name == "Pair":
                if result.success:
                    self._mark_paired()
                    self._async_notify_paired()
                else:
                    self._pairing_pending = False
                    self._async_notify_pairing(
                        f"Pairing failed: {result.error_message}. Make sure the vehicle is "
                        "awake and in range of Bluetooth, then press the Pair button on the "
                        "device page to try again."
                    )
                self._async_update_pairing_issue()
            self._notify()

        return callback

    async def async_command(self, name: str, action: Callable[[Callable], None]) -> None:
        """Run a vehicle command, connecting first if needed."""
        async with self._work():
            await self._async_ensure_connected()
            self._last_activity = time.monotonic()
            self.last_command = f"{name}: pending"
            self._notify()
            action(self._make_result_callback(name))
            await self._transport.async_flush()

    async def async_pair(self) -> None:
        """Send the whitelist-add request; tap a key card on the console next."""
        async with self._work():
            if not self._vehicle.has_private_key:
                self._vehicle.regenerate_key()
                if not self._vehicle.has_private_key:
                    raise HomeAssistantError("Could not generate a vehicle private key")
            await self._store.async_flush()
            await self._async_ensure_connected()
            self._last_activity = time.monotonic()
            self.last_command = "Pair: waiting for key card tap"
            self._pairing_pending = True
            self._async_notify_pairing(
                f"Tap a key card on the console of {self.device_name} to authorize Home "
                "Assistant. The request times out after about 30 seconds."
            )
            self._notify()
            self._vehicle.pair(tesla_ble.KeysRole.OWNER, self._make_result_callback("Pair"))
            await self._transport.async_flush()

    async def async_force_update(self) -> None:
        async with self._work():
            await self._async_ensure_connected()
            self._last_activity = time.monotonic()
            self._vehicle.vcsec_poll()
            self._vehicle.infotainment_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)
            self._poll_policy.on_poll(monotonic_ms())
            await self._transport.async_flush()

    async def async_wake(self) -> None:
        await self.async_command("Wake", lambda cb: self._vehicle.wake(cb))

    async def async_lock_doors(self, lock: bool) -> None:
        await self.async_command(
            "Lock" if lock else "Unlock",
            lambda cb: self._vehicle.lock(cb) if lock else self._vehicle.unlock(cb),
        )

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

    async def async_set_climate_temp(self, temp_celsius: float) -> None:
        await self.async_command(
            "Set Climate Temp", lambda cb: self._vehicle.set_climate_temp(temp_celsius, cb)
        )

    async def async_set_climate_keeper(self, mode: int) -> None:
        names = {0: "Climate Keeper Off", 1: "Climate Keeper On", 2: "Climate Keeper Dog", 3: "Climate Keeper Camp"}
        await self.async_command(
            names.get(mode, "Climate Keeper"), lambda cb: self._vehicle.set_climate_keeper(mode, cb)
        )

    async def async_set_preconditioning_max(self, enable: bool) -> None:
        await self.async_command(
            "Defrost On" if enable else "Defrost Off",
            lambda cb: self._vehicle.set_preconditioning_max(enable, cb),
        )

    async def async_set_bioweapon_mode(self, enable: bool) -> None:
        await self.async_command(
            "Bioweapon On" if enable else "Bioweapon Off",
            lambda cb: self._vehicle.set_bioweapon_mode(enable, cb),
        )

    async def async_set_steering_wheel_heat(self, enable: bool) -> None:
        await self.async_command(
            "Steering Heat On" if enable else "Steering Heat Off",
            lambda cb: self._vehicle.set_steering_wheel_heat(enable, cb),
        )

    async def async_set_sentry_mode(self, enable: bool) -> None:
        await self.async_command(
            "Sentry On" if enable else "Sentry Off",
            lambda cb: self._vehicle.set_sentry_mode(enable, cb),
        )

    async def async_regenerate_key(self) -> None:
        async with self._work():
            await self._async_disconnect()
            self.paired = False
            self._store.remove("paired_key")
            self.last_command = "Regenerate Key: pending"
            self._notify()
            results = []
            self._vehicle.regenerate_key(results.append)
            self._async_update_pairing_issue()
            try:
                await self._store.async_flush()
            except HomeAssistantError:
                self.last_command = "Regenerate Key: failed (credentials not persisted)"
                self._notify()
                raise
            self._make_result_callback("Regenerate Key")(results[0])

    async def async_unlatch_driver_door(self) -> None:
        await self.async_command("Unlatch Driver Door", lambda cb: self._vehicle.unlatch_driver_door(cb))

    async def async_open_trunk(self) -> None:
        await self.async_command("Open Trunk", lambda cb: self._vehicle.open_trunk(cb))

    async def async_close_trunk(self) -> None:
        await self.async_command("Close Trunk", lambda cb: self._vehicle.close_trunk(cb))

    async def async_open_frunk(self) -> None:
        await self.async_command("Open Frunk", lambda cb: self._vehicle.open_frunk(cb))

    async def async_open_charge_port(self) -> None:
        await self.async_command("Open Charge Port", lambda cb: self._vehicle.open_charge_port(cb))

    async def async_close_charge_port(self) -> None:
        await self.async_command("Close Charge Port", lambda cb: self._vehicle.close_charge_port(cb))

    async def async_unlock_charge_port(self) -> None:
        await self.async_command("Unlock Charge Port", lambda cb: self._vehicle.unlock_charge_port(cb))

    async def async_vent_windows(self) -> None:
        await self.async_command("Vent Windows", lambda cb: self._vehicle.vent_windows(cb))

    async def async_close_windows(self) -> None:
        await self.async_command("Close Windows", lambda cb: self._vehicle.close_windows(cb))

    async def async_flash_lights(self) -> None:
        await self.async_command("Flash Lights", lambda cb: self._vehicle.flash_lights(cb))

    async def async_honk_horn(self) -> None:
        await self.async_command("Honk Horn", lambda cb: self._vehicle.honk_horn(cb))
