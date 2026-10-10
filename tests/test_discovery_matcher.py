"""Manifest discovery matchers must match real Tesla advertisements.

The sample data mirrors a live 2022 Model 3 (advertised name
``S93a7bbb50e28cfdbC`` and firmware 2026.x): the advertisement carries an
Apple-format iBeacon payload (constant Tesla proximity UUID plus per-vehicle
major/minor) and advertises the ``0x1122`` service, but *not* the ``0x0211``
GATT service UUID, which only appears after connecting. Tesla's own tooling
scans by the advertised name only, so these matchers mirror that signal.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from homeassistant.components.bluetooth.match import BluetoothMatcherIndex, ble_device_matches

MANIFEST = json.loads(
    (Path(__file__).parent.parent / "custom_components" / "tesla_ble" / "manifest.json").read_text()
)

BEACON = bytes.fromhex("021574278bdab64445208f0c720eaf05993501009970c5")
TESLA_UUID_1122 = "00001122-0000-1000-8000-00805f9b34fb"
TESLA_UUID_211 = "00000211-b2d1-43f0-9b88-960cebf8b91e"


def advert(**overrides) -> SimpleNamespace:
    base = dict(
        name=None,
        address="74:02:E1:39:BF:A1",
        connectable=True,
        service_uuids=set(),
        service_data={},
        manufacturer_data={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _matches(info: SimpleNamespace) -> bool:
    return any(ble_device_matches(matcher, info) for matcher in MANIFEST["bluetooth"])


def test_real_car_advert_matches_manifest() -> None:
    info = advert(
        name="S93a7bbb50e28cfdbC",
        service_uuids={TESLA_UUID_1122},
        manufacturer_data={76: BEACON},
    )
    assert _matches(info)


def test_passive_scanner_without_name_matches() -> None:
    """Passive-only proxies forward the beacon payload but may omit the name."""
    assert _matches(advert(manufacturer_data={76: BEACON}))


def test_advert_with_gatt_service_uuid_matches() -> None:
    assert _matches(advert(service_uuids={TESLA_UUID_211}))


def test_new_scheme_name_matches() -> None:
    assert _matches(advert(name="Tesla 123456"))


def test_unrelated_device_does_not_match() -> None:
    info = advert(name="Smart CO Alarm", manufacturer_data={76: b"\x02\x15\x00\x00"})
    assert not _matches(info)


def test_matchers_survive_ha_indexing() -> None:
    """HA buckets local-name matchers by their first three characters.

    A pattern whose first characters are not literal is filed under an
    unreachable bucket and silently never fires, so exercise the real index.
    """
    index = BluetoothMatcherIndex()
    for matcher in MANIFEST["bluetooth"]:
        index.add(matcher)
    index.build()

    assert index.match(advert(name="S93a7bbb50e28cfdbC", service_uuids={TESLA_UUID_1122}, manufacturer_data={76: BEACON}))
    assert index.match(advert(name="Tesla 123456"))
    assert index.match(advert(manufacturer_data={76: BEACON}))
    assert not index.match(advert(name="Smart CO Alarm"))
