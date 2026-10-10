# Tesla BLE for Home Assistant

Control a Tesla vehicle directly over Bluetooth Low Energy from Home Assistant —
no cloud, no API tokens. Works with the HA host's own Bluetooth adapter or with
[ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html),
so a small ESP32 near the car is enough.

This is the Home Assistant integration for the
[TeslaBLE](https://github.com/yoziru/tesla-ble) C++ library, driven through its
Python bindings (`tesla-ble` on PyPI). It is the successor to
[yoziru/esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble): instead
of flashing a custom ESPHome component, install this integration and use stock
ESPHome Bluetooth proxies for range.

## Features

- Local BLE communication with VCSEC (vehicle security) and Infotainment domains
- Pairing with a key card straight from Home Assistant
- State: battery, range, charging, climate, odometer, tire pressure, closures,
  sleep/presence state, lock state
- Controls: lock/unlock, charging on/off, charge limit and amps, climate,
  steering wheel heater, trunk/frunk/charge port/windows, wake, flash lights,
  honk horn
- Sleep-friendly polling: backs off automatically and disconnects when idle so
  the vehicle can go to sleep

## Requirements

- Home Assistant with the **Bluetooth** integration working (local adapter
  and/or ESPHome Bluetooth proxy with active connections enabled)
- A paired key: press **Pair** in Home Assistant, then tap a key card on the
  vehicle's center console within a few seconds
- The `tesla-ble` Python package (declared in the manifest; HACS installs it)

## Installation

1. HACS → Integrations → three-dot menu → *Custom repositories* → add this
   repository URL, category *Integration*.
2. Install "Tesla BLE" and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → Tesla BLE*.
   The vehicle is usually discovered automatically once its Bluetooth
   advertisement is seen; otherwise enter the VIN and the Bluetooth address
   (from HA's Bluetooth advertisement monitor or `scripts/carcheck.py --scan`).
4. If this is a new key: open the vehicle's device page and press **Pair**,
   then tap a key card on the center console reader.

> **Until `tesla-ble` is published to PyPI**, Home Assistant cannot install the
> requirement automatically. Build and install it first (from the repository
> root; requires a C++23 compiler and Python ≥ 3.13):
>
> ```sh
> uv sync --project /path/to/tesla-ble
> uv pip install --python <HA-venv>/bin/python /path/to/tesla-ble
> ```
>
> Prebuilt wheels (Linux x86_64/aarch64, macOS arm64/x86_64) are produced by
> the repository's cibuildwheel workflow; local wheels can be built with
> `uvx cibuildwheel --platform linux .` (macOS needs the python.org installers,
> see the bindings README).

## Testing without Home Assistant

`scripts/carcheck.py` talks to the vehicle directly from any machine with
Python and Bluetooth (macOS, Linux, or a laptop with a USB dongle):

```sh
pip install tesla-ble bleak
python scripts/carcheck.py --vin 5YJ... --scan     # find the car
python scripts/carcheck.py --vin 5YJ... --poll     # connect, poll, print JSON
python scripts/carcheck.py --vin 5YJ... --pair     # then tap a key card
```

## Entities

| Platform | Examples |
| --- | --- |
| `sensor` | battery level, range, charger power/voltage/current, charge limit, odometer, temperatures, tire pressures, charging state, shift state, last command |
| `binary_sensor` | asleep, charging, charger connected, user present, doors, windows, Bluetooth connection |
| `lock` | doors |
| `switch` | charging, steering wheel heater |
| `number` | charge limit, charging amps |
| `cover` | trunk, frunk, charge port door, windows |
| `button` | wake, pair, refresh data, flash lights, honk horn |

## Options

- **Vehicle status poll interval** (default 60 s)
- **Awake / charging poll intervals** and **idle back-off timeout**
- **Disconnect after idle** (default 120 s)
- **Maximum charging amps** (caps the charging amps slider)

Use the integration's **Reconfigure** menu to change a Bluetooth address while
keeping the VIN and credentials. Deleting the entry removes its local keys and
sessions; it does not remove the key from the vehicle's whitelist.

Keys remain plaintext/base64 in HA's `.storage`, in owner-only (0600), atomically
written files. Keep HA backups private. Pairing generates and durably saves the
key before sending it to the car. A key's existence alone does not confirm pairing:
the per-vehicle repair warning clears after successful pairing or a verified,
successful session handshake. Existing keys are retained during that check.

**Regenerate key** is disabled by default. Enabling and pressing it disconnects
the car, replaces the local key, drops sessions and requires pairing again. It
does not revoke the previous key on the vehicle.

Telemetry expires separately by state source: vehicle-security data after two
poll intervals (at least 120 seconds), infotainment data after two idle back-off
intervals (at least 120 seconds). Expired entities become unavailable; connection
status and pairing/control buttons remain usable. This is cached telemetry, not
a guarantee of the vehicle's current physical state.

See [TESTING.md](TESTING.md) for offline checks and a staged hardware checklist.

## Troubleshooting

- **Vehicle not found**: check that the Bluetooth integration sees the car
  (Settings → Devices & Services → Bluetooth → *Advertisement monitor*), that a
  Bluetooth proxy is in range, and that the Tesla/Tessie phone apps are not
  holding the (max. 3) BLE connections. Legacy Model S/X (pre-2021) are not
  supported.
- **Pairing fails**: press Pair, then tap the card within ~30 s. The *Last
  command* sensor reports the result.
- **Commands time out**: the vehicle may be asleep; press Wake first, or check
  the *Asleep* binary sensor.
- **Multiple vehicles**: add the integration once per vehicle; each entry has
  its own connection, storage and entities. The vehicle-side limit of ~3
  simultaneous BLE connections is *per car* (phone apps, watches, Charge on
  Solar) — the integration disconnects when idle to leave room, and one
  vehicle never blocks another.

## License

MIT
