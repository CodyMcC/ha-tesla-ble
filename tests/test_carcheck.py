"""Offline CLI completion and credential safety checks."""

import stat
from types import SimpleNamespace

import pytest
from test_manager import VIN, FakeGattClient

from scripts import carcheck


@pytest.mark.parametrize("timeout, expected", [(5.0, 0), (0.01, 1)])
async def test_pair_waits_for_result_and_timeout_fails(sim, monkeypatch, tmp_path, timeout, expected):
    class Client(FakeGattClient):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.disconnect()

    client = Client(sim)

    def connect(device, disconnected_callback):
        client._on_disconnect = disconnected_callback
        return client

    async def find(*args):
        return SimpleNamespace(address="AA:BB:CC:DD:EE:FF")

    monkeypatch.setattr(carcheck, "STATE_DIR", tmp_path)
    monkeypatch.setattr(carcheck, "find_device", find)
    monkeypatch.setattr(carcheck, "BleakClient", connect)
    args = SimpleNamespace(vin=VIN, address=None, scan_timeout=1, timeout=timeout, scan=False, debug=False,
                           pair=True, poll=False, wake=False, lock=False, unlock=False, charging=None, amps=None, limit=None)
    assert await carcheck.run(args) == expected
    assert not client.is_connected
    assert stat.S_IMODE((tmp_path / f"carcheck-{VIN}.json").stat().st_mode) == 0o600


def test_cli_storage_keeps_previous_key_on_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    store = carcheck.FileStorage(path)
    assert store.save("private_key", b"old")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(carcheck.os, "replace", fail)
    assert not store.save("private_key", b"new")
    assert store.load("private_key") == b"old"
    assert carcheck.FileStorage(path).load("private_key") == b"old"
