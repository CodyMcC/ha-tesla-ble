"""Config and options flow tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.components import bluetooth

from custom_components.tesla_ble.const import CONF_ADDRESS, CONF_VIN, DOMAIN

VIN = "5YJ30123456789ABC"
ADDRESS = "AA:BB:CC:DD:EE:FF"
ADVERTISED = "S1a87a5a75f3df858C"  # for a different VIN


def _discovered(address: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(address=address, name=name)


async def test_manual_flow_creates_entry(hass) -> None:
    with patch.object(bluetooth, "async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["step_id"] == "vin"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: VIN})
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_VIN: VIN, CONF_ADDRESS: ADDRESS}
    assert result["title"] == "Tesla 789ABC"


async def test_manual_flow_rejects_bad_address(hass) -> None:
    with patch.object(bluetooth, "async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "nonsense"})
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_manual_flow_rejects_bad_vin(hass) -> None:
    with patch.object(bluetooth, "async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: "SHORT"})
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_VIN: "invalid_vin"}


async def test_user_flow_offers_discovered_devices(hass) -> None:
    discovered = _discovered(ADDRESS, ADVERTISED)
    with patch.object(bluetooth, "async_discovered_service_info", return_value=[discovered]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["step_id"] == "vin"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: VIN})
    # The discovered name belongs to a different VIN.
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_VIN: "name_mismatch"}


async def test_bluetooth_discovery_flow(hass) -> None:
    advertised = "S1a87a5a75f3df858C"
    # Derive the VIN whose advertised name this is: use the binding in reverse.
    import tesla_ble

    vin = next(
        v
        for v in ("5YJS0000000000000", VIN)
        if tesla_ble.get_vin_advertisement_name(v) == advertised
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovered(ADDRESS, advertised),
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "vin"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: vin})
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VIN] == vin


async def test_bluetooth_discovery_ignores_other_devices(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovered(ADDRESS, "SomeSpeaker"),
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_duplicate_vin_aborts(hass) -> None:
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Tesla 789ABC",
        data={CONF_VIN: VIN, CONF_ADDRESS: ADDRESS},
        source=config_entries.SOURCE_USER,
        unique_id=VIN,
        discovery_keys={},
        options={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)

    with patch.object(bluetooth, "async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "11:22:33:44:55:66"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: VIN})
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_apply_after_reload_and_reject_invalid_intervals(hass, sim, monkeypatch, hass_storage):
    from test_manager import setup_entry

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    old = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(result["flow_id"], {"vcsec_poll_interval": 0})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"vcsec_poll_interval": 120, "charging_amps_max": 16}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert old._stopped and entry.runtime_data is not old
    assert entry.runtime_data.vcsec_poll_interval == 120
    assert entry.runtime_data.charging_amps_max == 16


async def test_reconfigure_preserves_vin_and_credentials(hass, sim, monkeypatch, hass_storage):
    from test_manager import setup_entry

    entry, _ = await setup_entry(hass, sim, monkeypatch, hass_storage)
    old_key = entry.runtime_data._store.load("private_key")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "reconfigure"
    with patch.object(bluetooth, "async_ble_device_from_address", return_value=None):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "11-22-33-44-55-66"})
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.data == {CONF_VIN: VIN, CONF_ADDRESS: "11:22:33:44:55:66"}
    assert entry.runtime_data._store.load("private_key") == old_key


async def test_duplicate_address_aborts(hass) -> None:
    """A second vehicle cannot claim the same Bluetooth address."""
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Tesla 789ABC",
        data={CONF_VIN: VIN, CONF_ADDRESS: ADDRESS},
        source=config_entries.SOURCE_USER,
        unique_id=VIN,
        discovery_keys={},
        options={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)

    with patch.object(bluetooth, "async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VIN: "5YJ30123456789DEF"})
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
