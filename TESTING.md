# Testing Tesla BLE

## Offline checks

The integration declares its published native dependency in `manifest.json`;
Home Assistant installs it automatically. For manual wheel testing, use a wheel
matching **HA's Python runtime and architecture**, not the development host.

Run the locked development environment from this repository:

```sh
uv sync --locked
uv run --no-sync ruff check custom_components tests scripts
uv run --no-sync ruff format --check custom_components tests scripts
uv run --no-sync mypy
uv run --no-sync pytest tests -q
uv run --no-sync python scripts/carcheck.py --help
git diff --check
```

The suite exercises real transport connect/subscribe/flush/disconnect code with
only the Bluetooth lookup and connector replaced. It covers simulator-backed
commands/entities, asynchronous chunk ordering, transport failures, stale-client
callbacks, slow connect/unload races, service cancellation, 32-bit clock wrap,
telemetry expiry, poll-queue backpressure, credential persistence before pairing, disk-error propagation,
real storage permissions, pairing/rekey/reload, entry removal, diagnostics,
options/address reconfiguration and CLI completion/timeouts.

These tests do **not** validate radio timing, firmware behavior, proxy indication
subscription or vehicle-specific physical controls.

The lockfile selects the runtime test environment. See the README for the
minimum HA version and the scope of compatibility validation.
Strict mypy covers BLE transport, credential storage adapters and constants;
other modules retain runtime integration coverage.

## Standalone vehicle CLI

Without Home Assistant, use Python 3.13+ on a supported native-wheel platform
with Bluetooth:

```sh
pip install tesla-ble bleak
python scripts/carcheck.py --vin <VIN> --scan
python scripts/carcheck.py --vin <VIN> --poll
python scripts/carcheck.py --vin <VIN> --pair
```

## Hardware checks — run manually

Close competing vehicle apps where practical. Have a key card available. Test
while parked, with sufficient clearance; do not remotely operate doors, windows
or charging hardware without observing the vehicle.

```sh
.venv/bin/python scripts/carcheck.py --vin <VIN> --scan
.venv/bin/python scripts/carcheck.py --vin <VIN> --pair --timeout 60
.venv/bin/python scripts/carcheck.py --vin <VIN> --poll --timeout 60
```

The CLI uses its own key file (`~/.tesla-ble/carcheck-<VIN>.json`), not HA's key.
Pair HA separately. `--poll` does not intentionally wake a sleeping vehicle;
use `--wake` first if an awake snapshot is needed. Failed/incomplete requests
return a nonzero exit code. Debug logs and snapshots may contain private data.

1. **Local HA adapter:** install the current bindings into the HA Python
   environment, copy `custom_components/tesla_ble`, restart HA, add the vehicle.
   Pairing starts automatically; hold a key card against the console reader when
   the notification prompts. If the vehicle was not reachable at setup, press
   **Pair** on its device page — progress and results arrive as persistent
   notifications. Confirm the repair warning clears and telemetry arrives.
2. **Stock ESPHome proxy:** repeat using a stock proxy with active Bluetooth
   connections enabled and a free connection slot. Check subscription and full
   polling, not just advertisement discovery. Release the local connection first.
3. **Observed controls:** test lock/unlock, charging and climate separately.
   Verify both the physical result and the updated HA state. In particular test
   Defrost → Normal, lock telemetry, and charge-port/latch behavior.
4. **Connection recovery:** let the car leave range or disable its proxy, then
   restore it. Confirm old telemetry expires, the connection sensor reports off,
   recovery works, and there are no overlapping/abandoned proxy connections.
5. **Unload/reload:** reload options while connection attempts or a command are
   pending. The old manager must release its link; only the new manager may poll.
   Cleanup may wait for an already-running connector attempt to return before
   disposing its client.
6. **Sleep:** observe an idle, uncharged car without sentry. Confirm polls back
   off, no deliberate wake loops persist, and idle disconnect releases its slot.
7. **Multiple vehicles:** ensure pairing issues, entities and connections remain
   independent. Reconfigure one address and verify its key survives. Do not use
   Regenerate key unless intentionally replacing that credential.

Before sharing diagnostics, verify VIN/address are redacted. Do not publish
credential files or unreviewed debug logs. A removed HA entry's vehicle-side key
must be revoked manually from the vehicle if desired.
