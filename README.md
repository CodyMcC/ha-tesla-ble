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
- A paired key: setup pairs automatically; to retry, enable **Pair vehicle**
  in the device's entity settings, press it, then tap a key card on the console
- The `tesla-ble` Python package (declared in the manifest; HACS installs it)

## Installation

1. HACS → Integrations → three-dot menu → *Custom repositories* → add this
   repository URL, category *Integration*.
2. Install "Tesla BLE" and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → Tesla BLE*.
   The vehicle is discovered automatically from its Bluetooth advertisement;
   pick it, enter the VIN, then follow the pairing step. If it is not
   discovered (e.g. only passive scanners are nearby), enter the VIN and the
   Bluetooth address manually (from HA's Bluetooth advertisement monitor or
   `scripts/carcheck.py --scan`).
4. Pairing starts right after the vehicle is added: hold a key card against
   the center console reader when prompted. If the vehicle was not reachable,
   enable **Pair vehicle** in its entity settings and press it to retry — progress and the result are
   shown as persistent notifications.

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
| `sensor` | battery, range, energy (kWh), power (W, estimated from volts/amps/phases; reported power and electrical limits are attributes), charge completion time, odometer, temperatures, tire pressures, charging state, charge limit reason, IEC 61851 state, gear |
| `update` | vehicle firmware: availability, status, release link, download/install progress and installation |
| `binary_sensor` | asleep, charging, charge cable connected, connection, doors (with per-door attributes), scheduled charging/departure, user present, parked |
| `device_tracker` | home while the car is within Bluetooth range, not_home once it leaves |
| `lock` | doors, charge port latch |
| `switch` | charging, steering wheel heater, sentry mode, polling |
| `number` | charge limit, charging amps (ranges come from the vehicle) |
| `select` | fitted seat heating/cooling levels, cabin overheat protection and activation temperature |
| `cover` | charge port door, trunk, frunk, windows (with per-window attributes) |
| `button` | wake up, refresh vehicle data, flash lights, honk horn |

There is one **Power** sensor in watts and one **Energy** sensor in kWh. Energy
starts at zero with the first observed session reading, then accumulates observed
increases in the vehicle's session-energy counter, including observed session
resets. Its total and last session reading are stored together across reloads,
restarts, and key regeneration; deleting the vehicle removes the counter. For
existing vehicles, tracking starts when this feature is installed, not retroactively
from the original installation date. `tracking_since` and `session_energy_kwh`
attributes identify the tracking period and current session. It is energy added
to the battery, not wall consumption: BLE has no lifetime meter. Completely missed
sessions, and resets where the new session already exceeds the last reading,
cannot be reconstructed. Writes are atomic and delayed by one second; an abrupt
power loss can lose that last unsaved observation, not the entire total.

The charge limit is exposed once, as a readable number control. Doors have one
open/closed binary sensor with per-door attributes, separate from the door lock
because locked and closed are different states. Windows have one cover combining
vent/close controls, aggregate state and per-window attributes; there are no
individual door/window sensors or separate window switches/sensors.
Phases, charger/vehicle current limits, requested current, BLE signal, **Pair vehicle**,
**Regenerate key**, and **Unlatch driver door** are disabled by default. Enable
them in the device's entity settings when needed; setup pairs automatically.
Existing entity IDs and custom names are retained, except retired duplicates.
**IEC 61851 state** and **Charge limit reason** are ordinary sensors enabled by default.
**Voltage** and **Current** (actual draw) are also enabled ordinary
sensors. Phases, limits and requested current remain available in Power's attributes,
with their separate diagnostic sensors disabled by default.
The presence tracker is named **Location**; firmware and polling entities are
named **Firmware** and **Polling**.

### Fitted climate and roof controls

Each seat has one climate select, matching the vehicle: Off, three heat levels,
and — only for seats the vehicle reports as ventilated — three cooling levels.
The icon follows the reported direction (heating/cooling). Heat/cool selections
send one request; the vehicle disables Auto and clears the opposite direction.
Off clears supported cooling, then heating, independently of stale levels.
A failure or disconnect cancels later steps; rapid selections cannot interleave
the Off sequence. Already acknowledged changes are not rolled back.
Seat controls are optional preconditioning extras: climate/preconditioning or
Climate Keeper must already be on; selecting a seat mode does not turn it on.
Auto controls automatic seat
climate, not just heating. A lost reply can leave the outcome unknown; the last
command sensor says so instead of claiming that the vehicle did nothing.

Seat climate (including Auto in the dropdown), steering-wheel heat, cabin
overheat protection, its temperature selector, and the sunroof cover are created
only after the vehicle reports their corresponding optional fields. An explicit
zero/false state is treated as evidence of support, not an exact fitted inventory. Observed support is stored
across reloads/restarts; missing fields in a partial response never delete an
already detected feature. Controls show reported state, never an optimistic
command-acknowledgment state.

Cabin protection supports Off/On and Fan only when the vehicle reports support;
activation temperature uses Low/Medium/High protocol levels without inventing
physical temperatures. Its active/allowed status and not-running reason are
attributes.

The sunroof is a position-capable cover (open/close/set position), not a binary
sensor; it does not appear on cars that never report sunroof telemetry. The public
BLE schema reports steering-wheel Auto/Low/High state but defines only an on/off
command: a mode sensor reports that state, while the existing switch controls
on/off. No unsupported mode-selection command is fabricated.

BLE has no vehicle-configuration endpoint for exact model/trim/year or an option
inventory. VIN information cannot reliably establish fitted hardware. Capability
discovery is observation-based: until a field is reported, support is unknown,
not definitively absent. The C++ library owns commands, validation, protocol state
decoding and delayed no-wake refreshes; HA owns feature memory/discovery, entity
types, labels and availability. ESPHome can use the same new Vehicle APIs without
reimplementing wire encoding; no ESPHome firmware deployment is needed for HA.

BLE does not report the installed firmware version: the update entity labels it
**Unknown**. With no pending update both version fields are Unknown and the entity
is off; an offered update uses its reported version (or **Pending update** if no
version is supplied). No firmware version is inferred or remembered as installed.
Installation is offered only for a freshly reported, downloaded update.
HA has no native update-cancel control; use the entity-targeted
`tesla_ble.cancel_software_update` action to cancel a schedule or download. It
cannot cancel an installation already running. There are no separate update
sensors or install/cancel buttons.

Entity names follow the popular Fleet API integration
([`tesla_custom`](https://github.com/alandtse/tesla)) wherever an equivalent
exists, so entity IDs are familiar and automations can be ported over. BLE-only
extras (tire pressures, charge-limit reason,
cable latch, BLE signal, pairing buttons) remain as additional entities.

## Options

- **Vehicle status poll interval** (default 60 s)
- **Awake / charging poll intervals** and **idle back-off timeout**
- **Disconnect after idle** (default 120 s)

HA's system option **Enable polling for changes** only controls HA's entity
polling scheduler. All Tesla BLE entities set `should_poll=False`, so that
scheduler is never started and the option does not affect this integration's
own manager timer. Use **Polling** to pause/resume automatic polling;
manual refresh and vehicle commands still work while paused.

The manager ticks every 500 ms to process queued work, not to query the car every
500 ms. Default query intervals are 60 s for vehicle-security status, 30 s for
awake infotainment, 10 s while charging/sentry is active, and 660 s once asleep
or idle long enough. Automatic infotainment and manual telemetry refresh use
**NO_WAKE_SKIP**: they never send a wake command. Explicit controls can wake the
car. No-wake does not mean zero battery use: BLE connections and queries still
cost energy and can influence sleep; overnight behavior needs hardware validation.

### History and long-term statistics

Recommended long-term statistics: **Energy**, **Power**, battery level, range,
odometer, and cabin/outside temperatures; tire-pressure trends are optional.
HA keeps their statistics independently of its raw-history retention setting.
Keep ordinary history for charging state, presence, door/window/lock state,
climate, charge limits and firmware status when useful for automations/debugging.
These non-numeric states do not get numeric long-term statistics.

Exclude last-command text, connection chatter, BLE signal, low-level electrical
diagnostics, polling and action buttons from Recorder unless troubleshooting.
Transient sensor attributes (session readings, electrical details and raw values)
are available live but excluded from Recorder to avoid redundant history.
Per-door/window attributes remain recorded so closure changes are traceable.
The integration does not modify global Recorder retention/exclusions or delete
existing history; those remain your HA configuration choices.

The charging-amps slider range follows the vehicle's own limit
(`charge_current_request_max`); no configuration is needed.

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
intervals (at least 120 seconds). While the vehicle is **observed asleep**, its
last known infotainment values stay visible instead of going unavailable (the
*Asleep* sensor shows on). When the vehicle **leaves Bluetooth range**, all
telemetry expires to *unavailable* about two minutes after the last contact,
the *Location* tracker switches to *not_home* and the *Connection*
binary sensor turns off. Use the tracker or the connection sensor for
automations; `unavailable` is a data-quality signal, not a presence signal.
Connection status and pairing/control buttons remain usable. This is cached
telemetry, not a guarantee of the vehicle's current physical state. The persisted
Energy total remains available even when the vehicle is away.

See [TESTING.md](TESTING.md) for offline checks and a staged hardware checklist.

## Troubleshooting

- **Vehicle not found**: check that the Bluetooth integration sees the car
  (Settings → Devices & Services → Bluetooth → *Advertisement monitor*), that a
  Bluetooth proxy is in range, and that the Tesla/Tessie phone apps are not
  holding the (max. 3) BLE connections. Discovery requires the vehicle to keep
  advertising for about 30 seconds, so a car merely passing by is ignored.
  Legacy Model S/X (pre-2021) are not supported. If the car was scanned by
  BlueZ *before* this integration was installed, its unchanged advertisement
  may never be re-emitted; clear the stale entry once with
  `bluetoothctl remove <car-mac>` and the car is rediscovered on the next
  advertisement.
- **Pairing fails**: enable and press Pair vehicle, then tap the card within ~30 s. The *Last
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
