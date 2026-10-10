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
   (visible on the car's Bluetooth settings screen, or with `scripts/carcheck.py --scan`).
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
> Prebuilt wheels for common platforms are on the roadmap (cibuildwheel
> workflow is in the repository).

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

## License

MIT
