"""Config and options flow for the Tesla BLE integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

import tesla_ble

from .const import (
    CONF_ADDRESS,
    CONF_CHARGING_AMPS_MAX,
    CONF_DISCONNECT_DELAY,
    CONF_INFOTAINMENT_POLL_INTERVAL_ACTIVE,
    CONF_INFOTAINMENT_POLL_INTERVAL_AWAKE,
    CONF_INFOTAINMENT_SLEEP_TIMEOUT,
    CONF_VCSEC_POLL_INTERVAL,
    CONF_VIN,
    DEFAULT_CHARGING_AMPS_MAX,
    DEFAULT_DISCONNECT_DELAY,
    DEFAULT_INFOTAINMENT_POLL_INTERVAL_ACTIVE,
    DEFAULT_INFOTAINMENT_POLL_INTERVAL_AWAKE,
    DEFAULT_INFOTAINMENT_SLEEP_TIMEOUT,
    DEFAULT_VCSEC_POLL_INTERVAL,
    DOMAIN,
    MAX_CHARGING_AMPS_MAX,
    MAX_DISCONNECT_DELAY,
    MAX_INFOTAINMENT_POLL_INTERVAL,
    MAX_SLEEP_TIMEOUT,
    MAX_VCSEC_POLL_INTERVAL,
    MIN_CHARGING_AMPS_MAX,
    MIN_DISCONNECT_DELAY,
    MIN_INFOTAINMENT_POLL_INTERVAL,
    MIN_SLEEP_TIMEOUT,
    MIN_VCSEC_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

MANUAL_ADDRESS = "__manual__"

VIN_SCHEMA = vol.Schema({vol.Required(CONF_VIN): str})


def _normalize_address(value: str) -> str:
    return value.strip().upper().replace("-", ":")


class TeslaBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Tesla vehicle, discovered over Bluetooth or entered manually."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._advertised_name: str | None = None
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """A Bluetooth advertisement matched the Tesla service UUID."""
        if not tesla_ble.is_tesla_vehicle_name(discovery_info.name or ""):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._advertised_name = discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_vin()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a discovered vehicle or enter its address manually."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL_ADDRESS:
                return await self.async_step_manual()
            self._address = address
            self._advertised_name = self._discovered.get(address)
            return await self.async_step_vin()

        configured_addresses = {
            entry.data.get(CONF_ADDRESS) for entry in self._async_current_entries(include_ignore=False)
        }
        for info in bluetooth.async_discovered_service_info(self.hass):
            if info.address in configured_addresses or not info.name:
                continue
            if not tesla_ble.is_tesla_vehicle_name(info.name):
                continue
            self._discovered[info.address] = info.name

        if not self._discovered:
            return await self.async_step_manual()

        choices = {
            address: f"{name} ({address})" for address, name in sorted(self._discovered.items())
        }
        choices[MANUAL_ADDRESS] = "Enter Bluetooth address manually"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)}),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual Bluetooth address entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = _normalize_address(user_input[CONF_ADDRESS])
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                self._address = address
                self._advertised_name = None
                return await self.async_step_vin()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    async def async_step_vin(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the VIN and validate it against the advertised name."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {
            "expected_name": "",
            "advertised_name": self._advertised_name or "",
            "address": self._address or "",
        }

        if user_input is None and self._advertised_name is None and self._address is not None:
            # Look up the currently advertised name for a manual address.
            device = bluetooth.async_ble_device_from_address(self.hass, self._address, connectable=True)
            if device is not None and device.name:
                self._advertised_name = device.name
                placeholders["advertised_name"] = device.name

        if user_input is not None:
            vin = user_input[CONF_VIN].strip().upper()
            if not VIN_RE.match(vin):
                errors[CONF_VIN] = "invalid_vin"
            else:
                await self.async_set_unique_id(vin)
                self._abort_if_unique_id_configured()
                for existing in self._async_current_entries(include_ignore=False):
                    if existing.data.get(CONF_ADDRESS) == self._address:
                        return self.async_abort(reason="already_configured")
                placeholders["expected_name"] = tesla_ble.get_vin_advertisement_name(vin)
                if self._advertised_name and not tesla_ble.matches_vin(self._advertised_name, vin):
                    errors[CONF_VIN] = "name_mismatch"
                if not errors:
                    return self.async_create_entry(
                        title=f"Tesla {vin[-6:]}",
                        data={CONF_VIN: vin, CONF_ADDRESS: self._address},
                    )

        return self.async_show_form(
            step_id="vin",
            data_schema=VIN_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TeslaBLEOptionsFlow()


class TeslaBLEOptionsFlow(OptionsFlow):
    """Polling and connection tuning."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_VCSEC_POLL_INTERVAL,
                    default=options.get(CONF_VCSEC_POLL_INTERVAL, DEFAULT_VCSEC_POLL_INTERVAL),
                ): vol.All(int, vol.Range(min=MIN_VCSEC_POLL_INTERVAL, max=MAX_VCSEC_POLL_INTERVAL)),
                vol.Optional(
                    CONF_INFOTAINMENT_POLL_INTERVAL_AWAKE,
                    default=options.get(
                        CONF_INFOTAINMENT_POLL_INTERVAL_AWAKE, DEFAULT_INFOTAINMENT_POLL_INTERVAL_AWAKE
                    ),
                ): vol.All(int, vol.Range(min=MIN_INFOTAINMENT_POLL_INTERVAL, max=MAX_INFOTAINMENT_POLL_INTERVAL)),
                vol.Optional(
                    CONF_INFOTAINMENT_POLL_INTERVAL_ACTIVE,
                    default=options.get(
                        CONF_INFOTAINMENT_POLL_INTERVAL_ACTIVE, DEFAULT_INFOTAINMENT_POLL_INTERVAL_ACTIVE
                    ),
                ): vol.All(int, vol.Range(min=MIN_INFOTAINMENT_POLL_INTERVAL, max=MAX_INFOTAINMENT_POLL_INTERVAL)),
                vol.Optional(
                    CONF_INFOTAINMENT_SLEEP_TIMEOUT,
                    default=options.get(CONF_INFOTAINMENT_SLEEP_TIMEOUT, DEFAULT_INFOTAINMENT_SLEEP_TIMEOUT),
                ): vol.All(int, vol.Range(min=MIN_SLEEP_TIMEOUT, max=MAX_SLEEP_TIMEOUT)),
                vol.Optional(
                    CONF_DISCONNECT_DELAY,
                    default=options.get(CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY),
                ): vol.All(int, vol.Range(min=MIN_DISCONNECT_DELAY, max=MAX_DISCONNECT_DELAY)),
                vol.Optional(
                    CONF_CHARGING_AMPS_MAX,
                    default=options.get(CONF_CHARGING_AMPS_MAX, DEFAULT_CHARGING_AMPS_MAX),
                ): vol.All(int, vol.Range(min=MIN_CHARGING_AMPS_MAX, max=MAX_CHARGING_AMPS_MAX)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
