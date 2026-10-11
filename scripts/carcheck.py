#!/usr/bin/env python3
"""carcheck: talk to a Tesla over BLE from the command line, without Home Assistant.

Requires: pip install tesla-ble bleak

Examples:
    # find the vehicle's Bluetooth address (scan for its advertised name)
    python carcheck.py --vin 5YJ3E1EA1KF000000 --scan

    # connect, poll all state, print JSON, disconnect
    python carcheck.py --vin 5YJ3E1EA1KF000000 --poll

    # pair a new key: sends the request; tap a key card on the console next
    python carcheck.py --vin 5YJ3E1EA1KF000000 --pair

    # commands
    python carcheck.py --vin 5YJ3E1EA1KF000000 --lock
    python carcheck.py --vin 5YJ3E1EA1KF000000 --unlock
    python carcheck.py --vin 5YJ3E1EA1KF000000 --charging on
    python carcheck.py --vin 5YJ3E1EA1KF000000 --amps 16
    python carcheck.py --vin 5YJ3E1EA1KF000000 --limit 80

State and keys are kept in ~/.tesla-ble/carcheck-<VIN>.json between runs.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import tesla_ble
from bleak import BleakClient, BleakScanner

READ_UUID = "00000213-b2d1-43f0-9b88-960cebf8b91e"
WRITE_UUID = "00000212-b2d1-43f0-9b88-960cebf8b91e"
WRITE_CHUNK_SIZE = 18

STATE_DIR = Path.home() / ".tesla-ble"


class FileStorage(tesla_ble.StorageAdapter):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._data: dict[str, str] = {}
        if path.exists():
            self._data = json.loads(path.read_text())
            os.chmod(path, 0o600)

    def load(self, key: str) -> bytes | None:
        value = self._data.get(key)
        return None if value is None else base64.b64decode(value, validate=True)

    def save(self, key: str, data: bytes) -> bool:
        return self._persist({**self._data, key: base64.b64encode(data).decode("ascii")})

    def remove(self, key: str) -> bool:
        if key not in self._data:
            return False
        return self._persist({k: v for k, v in self._data.items() if k != key})

    def _persist(self, data: dict[str, str]) -> bool:
        temporary = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=self._path.parent, delete=False) as file:
                temporary = file.name
                json.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._path)
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._data = data
            return True
        except OSError:
            logging.exception("Could not persist vehicle credentials")
            return False
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


class QueueAdapter(tesla_ble.BleAdapter):
    """Collects frames; the ticker writes them over GATT."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[bytes] = []

    def connect(self, address: str) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def write(self, data: bytes) -> bool:
        self.queue.append(bytes(data))
        return True


def vehicle_name_candidates(vin: str) -> list[str]:
    return [tesla_ble.get_vin_advertisement_name(vin), f"Tesla {vin[-6:]}"]


async def find_device(vin: str, address: str | None, scan_timeout: float = 10.0):
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=scan_timeout)
        if device is None:
            sys.exit(f"Device {address} not found")
        return device

    print(f"Scanning {scan_timeout:.0f}s for {vehicle_name_candidates(vin)} ...")
    found = asyncio.Event()
    result: dict[str, Any] = {}

    def on_detect(device, adv):
        if tesla_ble.matches_vin(adv.local_name or device.name or "", vin):
            result["device"] = device
            found.set()

    try:
        async with BleakScanner(detection_callback=on_detect):
            await asyncio.wait_for(found.wait(), timeout=scan_timeout)
    except TimeoutError:
        sys.exit("Vehicle not found. Is it in range and are other BLE connections (apps) closed?")
    device = result["device"]
    print(f"Found {device.name} at {device.address}")
    return device


async def run(args: argparse.Namespace) -> int:
    vin = args.vin.strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        raise ValueError("VIN must contain 17 valid VIN characters")
    storage = FileStorage(STATE_DIR / f"carcheck-{vin}.json")
    adapter = QueueAdapter()
    vehicle = tesla_ble.Vehicle(adapter, storage)
    vehicle.set_vin(vin)

    states: dict[str, Any] = {}
    command_results: list[tesla_ble.CommandResult] = []

    vehicle.set_vehicle_status_callback(lambda s: states.update(vehicle_status=s))
    vehicle.set_charge_state_callback(lambda s: states.update(charge_state=s))
    vehicle.set_climate_state_callback(lambda s: states.update(climate_state=s))
    vehicle.set_drive_state_callback(lambda s: states.update(drive_state=s))
    vehicle.set_closures_state_callback(lambda s: states.update(closures_state=s))
    vehicle.set_tire_pressure_state_callback(lambda s: states.update(tire_pressure_state=s))

    if args.debug:
        tesla_ble.set_log_callback(
            lambda level, message, tag, line: print(f"[{level}] {tag}: {message}", file=sys.stderr)
        )

    device = await find_device(vin, args.address, args.scan_timeout)
    if args.scan:
        return 0
    if not args.pair and not vehicle.has_private_key:
        print("No saved key. Run --pair and tap a key card first.", file=sys.stderr)
        return 1

    disconnected = asyncio.Event()

    def on_disconnect(_client: BleakClient) -> None:
        disconnected.set()

    print(f"Connecting to {device.address} ...")
    async with BleakClient(device, disconnected_callback=on_disconnect) as client:
        await client.start_notify(READ_UUID, lambda _sender, data: vehicle.on_rx_data(bytes(data)))
        vehicle.set_connected(True)

        if args.wake:
            vehicle.wake(command_results.append)
        if args.lock:
            vehicle.lock(command_results.append)
        if args.unlock:
            vehicle.unlock(command_results.append)
        if args.charging is not None:
            vehicle.set_charging_state(args.charging, command_results.append)
        if args.amps is not None:
            vehicle.set_charging_amps(args.amps, command_results.append)
        if args.limit is not None:
            vehicle.set_charging_limit(args.limit, command_results.append)
        if args.pair:
            print("Pairing: tap a key card on the center console now ...")
            vehicle.pair(tesla_ble.KeysRole.OWNER, command_results.append)
        polling = args.poll or not (
            args.lock
            or args.unlock
            or args.pair
            or args.wake
            or args.charging is not None
            or args.amps is not None
            or args.limit is not None
        )
        if polling:
            vehicle.vcsec_poll()
            vehicle.infotainment_poll(tesla_ble.WakePolicy.NO_WAKE_SKIP)

        completed = False
        deadline = time.monotonic() + args.timeout
        try:
            while time.monotonic() < deadline and not disconnected.is_set():
                vehicle.loop()
                while adapter.queue:
                    frame = adapter.queue.pop(0)
                    for offset in range(0, len(frame), WRITE_CHUNK_SIZE):
                        await client.write_gatt_char(
                            WRITE_UUID, frame[offset : offset + WRITE_CHUNK_SIZE], response=False
                        )
                if vehicle.pending_command_count == 0:
                    completed = bool(states) if polling else bool(command_results)
                    break
                await asyncio.sleep(0.1)
        finally:
            vehicle.set_connected(False)
        if not completed:
            print("Vehicle disconnected or did not complete the request before the timeout.", file=sys.stderr)

    for result in command_results:
        status = "ok" if result.success else ("skipped" if result.skipped else "FAILED")
        detail = "" if result.error_message is None else f" - {result.error_message}"
        print(f"Command: {status}{detail}")

    print(json.dumps(states, indent=2, default=str))
    return 0 if completed and not any(result.failure for result in command_results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vin", required=True, help="17-character VIN")
    parser.add_argument("--address", help="Bluetooth address (skips name scan)")
    parser.add_argument("--scan", action="store_true", help="Only scan for the vehicle and exit")
    parser.add_argument("--poll", action="store_true", help="Poll all state (default action)")
    parser.add_argument("--wake", action="store_true", help="Wake the vehicle")
    parser.add_argument("--lock", action="store_true")
    parser.add_argument("--unlock", action="store_true")
    parser.add_argument("--pair", action="store_true", help="Send pairing request, then tap a key card")
    parser.add_argument("--charging", choices=["on", "off"])
    parser.add_argument("--amps", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=60.0, help="Maximum seconds to complete queued requests")
    parser.add_argument("--debug", action="store_true", help="Print library log messages")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", args.vin.strip().upper()):
        parser.error("VIN must contain 17 valid VIN characters")
    if args.timeout <= 0 or args.scan_timeout <= 0:
        parser.error("Timeouts must be positive")

    if args.charging is not None:
        args.charging = args.charging == "on"

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
