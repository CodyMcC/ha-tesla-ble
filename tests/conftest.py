"""Pytest configuration for the Tesla BLE integration tests."""

import pytest
import tesla_ble

pytest_plugins = "pytest_homeassistant_custom_component"

VIN = "5YJ30123456789ABC"

# Protocol-spec test key (same as tesla-ble/tests/test_constants.h)
TEST_PRIVATE_KEY_PEM = (
    "-----BEGIN EC PRIVATE KEY-----\n"
    "MHcCAQEEICU4zcKal8GcHpmmN9bPT4yXDBGLVu3h5jI+bRYsSzDboAoGCCqGSM49\n"
    "AwEHoUQDQgAEsra8aMLaBmXOZWgVWUmWxiOU7di+qQX+eBp1T+aoRacUMwkC8iXp\n"
    "Jp1GbgWzSZgf2p2FzCPG+0RKpztikQXcbg==\n"
    "-----END EC PRIVATE KEY-----"
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ loadable in every test."""
    yield


@pytest.fixture(autouse=True)
def bluetooth_integration(enable_bluetooth):
    """Set up a mocked bluetooth integration (dependency of the integration)."""
    yield


@pytest.fixture
def sim() -> tesla_ble.SimulatedVehicle:
    sim = tesla_ble.SimulatedVehicle(VIN)
    sim.set_battery_level(55)
    sim.set_battery_range(210.5)
    sim.set_charging_state(tesla_ble.charging_state_tag("Charging"))
    sim.set_charge_limit(80)
    sim.set_charging_amps(16)
    sim.set_outside_temp(11.0)
    sim.set_odometer_miles(1234.5)
    return sim
