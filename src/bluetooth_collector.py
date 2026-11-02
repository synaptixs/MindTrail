#!/usr/bin/env python3
"""Optional BLE collector. Saves a separate, auditable local session.

Only authentication, START and STOP are transmitted. Device clock, saved EEG,
firmware and persistent settings are not changed by this collector.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ble_protocol as protocol

ROOT = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        json.dump(value, output, allow_nan=False)
    temporary.replace(path)


def bluetooth_library():
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as error:
        raise RuntimeError("Bluetooth environment is missing. Install requirements-bluetooth.txt into .venv-bluetooth.") from error
    return BleakClient, BleakScanner


async def scan_devices(seconds: float = 10) -> list[tuple[object, dict]]:
    _, scanner = bluetooth_library()
    try:
        discovered = await asyncio.wait_for(scanner.discover(timeout=seconds, return_adv=True), seconds+15)
    except asyncio.TimeoutError as error:
        raise RuntimeError("Bluetooth scan timed out while waiting for Bluetooth access. Check Bluetooth power and access permission, then retry.") from error
    result = []
    for device, advertisement in discovered.values():
        name = advertisement.local_name or device.name or ""
        services = [uuid.lower() for uuid in advertisement.service_uuids]
        if "AWEAR" not in name.upper() and protocol.SERVICE_UUID not in services:
            continue
        result.append((device, {"id": device.address, "name": name or "AWEAR device",
                                "rssi": advertisement.rssi, "serviceAdvertised": protocol.SERVICE_UUID in services}))
    return sorted(result, key=lambda item: item[1]["id"])


class Recorder:
    def __init__(self, output: Path, device_id: str, participant: str = "", placement: str = "unknown"):
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output = output
        self.handles = {}
        names = ("raw-notifications.jsonl", "eeg.jsonl", "events.jsonl", "eeg.csv")
        if any((output / name).exists() for name in (*names, "status.json", "session.json")):
            raise ValueError("Choose a new output directory; an earlier recording already exists here")
        try:
            for name in names:
                # Exclusive creation also protects against concurrent collectors.
                self.handles[name] = os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
        except OSError:
            for handle in self.handles.values():
                handle.close()
            raise
        self.csv = csv.writer(self.handles["eeg.csv"])
        self.csv.writerow(["device_time_utc", "received_at_utc", "received_unix_ns", "received_monotonic_ns",
                           "device_id", "counter", "format", "from_saved_memory", "missing_before",
                           "duplicate_counter", "placement", *[f"sample_{index:03}" for index in range(256)]])
        self.counters = protocol.CounterTracker()
        self.status = {"state": "scanning", "startedAt": utc_now(), "deviceId": device_id,
                       "participantId": participant, "placement": placement, "source": "bluetooth",
                       "sampleRate": 256, "units": "unmodified ADC device units", "clockChanged": False,
                       "clockAlignment": "unverified; device time and host arrival retained separately",
                       "protocolReferenceRevision": protocol.REFERENCE_REVISION,
                       "notifications": 0, "bytes": 0, "eegBlocks": 0, "samples": 0, "decodeErrors": 0,
                       "liveBlocks": 0, "replayedBlocks": 0, "savedMemoryDeletionReported": False,
                       "missingBlocks": 0, "duplicateCounters": 0, "queueOverflows": 0,
                       "latest": None, "error": None}
        self.publish()

    def line(self, name: str, value: dict) -> None:
        handle = self.handles[name]
        handle.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
        handle.flush()

    def publish(self) -> None:
        self.status["updatedAt"] = utc_now()
        atomic_json(self.output / "status.json", self.status)

    def event(self, kind: str, **details) -> None:
        self.line("events.jsonl", {"at": utc_now(), "kind": kind, **details})

    def received(self, data: bytes, received_ns: int, monotonic_ns: int) -> None:
        self.status["notifications"] += 1
        self.status["bytes"] += len(data)
        self.line("raw-notifications.jsonl", {"receivedUnixNs": received_ns,
                  "receivedMonotonicNs": monotonic_ns, "bytesBase64": base64.b64encode(data).decode()})

    def eeg(self, message: protocol.Message, received_ns: int, monotonic_ns: int) -> None:
        samples = protocol.decode_samples(message)
        header = message.header
        tracking = self.counters.check(header)
        device_time = (datetime.fromtimestamp(header.timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
                       if header.timestamp else None)
        received_at = datetime.fromtimestamp(received_ns / 1e9, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        row = {"deviceTime": device_time, "receivedAt": received_at, "receivedUnixNs": received_ns,
               "receivedMonotonicNs": monotonic_ns, "header": header.metadata(), **tracking,
               "source": "bluetooth", "sampleRate": 256, "samples": samples,
               "placement": "unknown" if header.replay else self.status["placement"]}
        self.line("eeg.jsonl", row)
        self.csv.writerow([device_time or "", received_at, received_ns, monotonic_ns,
                           f"{header.device_id:016X}", header.counter, header.format, int(header.replay),
                           tracking["missingBefore"], int(tracking["duplicateCounter"]), row["placement"], *samples])
        self.handles["eeg.csv"].flush()
        self.status["eegBlocks"] += 1
        self.status["replayedBlocks" if header.replay else "liveBlocks"] += 1
        self.status["samples"] += len(samples)
        self.status["missingBlocks"] += tracking["missingBefore"]
        self.status["duplicateCounters"] += int(tracking["duplicateCounter"])
        self.status["latest"] = row
        self.publish()

    def finish(self, state: str, error: str | None = None) -> None:
        self.status.update(state=state, error=error, endedAt=utc_now())
        self.event("session_finished", state=state, error=error)
        self.publish()
        summary = {key: value for key, value in self.status.items() if key != "latest"}
        summary["files"] = list(self.handles)
        atomic_json(self.output / "session.json", summary)
        for handle in self.handles.values():
            handle.close()


class RecordingStopped(Exception):
    """A cross-platform request to finalize the current session."""


async def record_stream(client, recorder: Recorder, seconds: float,
                        ready_timeout: float = 20, data_timeout: float = 12, stop_file: Path | None = None) -> None:
    """Transport-independent loop, exercised with a scripted BLE peer in tests."""
    tx = client.services.get_characteristic(protocol.TX_UUID)
    rx = client.services.get_characteristic(protocol.RX_UUID)
    if tx is None or rx is None or "notify" not in rx.properties:
        raise RuntimeError("Device does not expose the expected AWEAR streaming characteristics")
    if "write-without-response" not in tx.properties:
        raise RuntimeError("AWEAR command characteristic lacks write-without-response support")
    queue = asyncio.Queue(maxsize=4096)
    assembler = protocol.Assembler()
    reply = None
    started = False
    overflow = False
    deadline = time.monotonic() + ready_timeout
    last_eeg = None

    def notification(_sender, data):
        nonlocal overflow
        try:
            queue.put_nowait((bytes(data), time.time_ns(), time.monotonic_ns()))
        except asyncio.QueueFull:
            overflow = True
            recorder.status["queueOverflows"] += 1

    async def write(command: bytes, label: str) -> None:
        # No generic command passthrough is exposed to the dashboard.
        await asyncio.wait_for(client.write_gatt_char(tx, command, response=False), 5)
        recorder.event("command", name=label)

    recorder.status["state"] = "waiting_for_ready"
    recorder.publish()
    try:
        try:
            await asyncio.wait_for(client.start_notify(rx, notification), ready_timeout)
        except asyncio.TimeoutError as error:
            raise RuntimeError("Bluetooth subscription timed out. Check Bluetooth access and disconnect the mobile app.") from error
        while True:
            now = time.monotonic()
            if stop_file and stop_file.exists():
                raise RecordingStopped()
            if overflow:
                raise RuntimeError("Notification queue overflowed; recording stopped to avoid an unreported gap")
            if not client.is_connected:
                raise RuntimeError("Bluetooth disconnected. Partial recording has been retained; reconnect to start a new session.")
            if now >= deadline:
                if not started:
                    raise RuntimeError("AWEAR authentication timed out. Disconnect the mobile app and retry.")
                if not recorder.status["eegBlocks"]:
                    raise RuntimeError("Device connected but no valid 256-sample EEG blocks arrived")
                break
            if started and now - (last_eeg or (deadline-seconds)) > data_timeout:
                raise RuntimeError("No valid EEG blocks arrived within the data timeout; partial recording retained")
            try:
                data, received_ns, monotonic_ns = await asyncio.wait_for(queue.get(), min(1, deadline-now))
            except asyncio.TimeoutError:
                recorder.publish()
                continue
            recorder.received(data, received_ns, monotonic_ns)
            try:
                # Some firmware emits handshake text without LUCA framing.
                if assembler.header is None and data.startswith((b"AWEAR_CONNECTED:", b"AWEAR_READY:")):
                    message, errors = None, []
                    text = data.decode("utf-8").strip("\x00\r\n ")
                else:
                    message, errors = assembler.feed(data)
                    for error in errors:
                        recorder.status["decodeErrors"] += 1
                        recorder.event("decode_warning", message=error)
                    if message is None:
                        continue
                    if message.header.format in protocol.EEG_FORMATS:
                        if not started:
                            recorder.event("eeg_before_ready_skipped")
                            continue
                        recorder.eeg(message, received_ns, monotonic_ns)
                        last_eeg = time.monotonic()
                        continue
                    text = message.payload.decode("utf-8").strip("\x00\r\n ")
                if text.startswith("AWEAR_CONNECTED:"):
                    if started:
                        raise RuntimeError("Device restarted its handshake during streaming; partial recording retained")
                    reply = protocol.challenge_reply(text.split(":", 1)[1])
                    await write(reply, "authentication_reply")
                elif text.startswith("AWEAR_READY:"):
                    if started:
                        continue
                    try:
                        info = protocol.ready_info(text, reply)
                    except protocol.ProtocolError as error:
                        raise RuntimeError(str(error)) from error
                    recorder.status["deviceInfo"] = info
                    recorder.event("device_ready", **info)
                    await write(b"START", "START")
                    started = True
                    deadline = time.monotonic() + seconds
                    recorder.status["state"] = "recording"
                    recorder.publish()
                elif "LOW_POWER_DISCONNECT" in text:
                    raise RuntimeError("AWEAR reported low battery and is disconnecting")
                else:
                    recorder.event("device_text", text=text[:2048])
                    if "Saved EEG data deleted:" in text:
                        recorder.status["savedMemoryDeletionReported"] = True
                        recorder.publish()
            except (protocol.ProtocolError, UnicodeError) as error:
                recorder.status["decodeErrors"] += 1
                recorder.event("decode_warning", message=str(error))
                recorder.publish()
    finally:
        if assembler.header is not None:
            recorder.status["decodeErrors"] += 1
            recorder.event("incomplete_message_at_end")
        if client.is_connected:
            try:
                if started:
                    await asyncio.wait_for(write(b"STOP", "STOP"), 3)
                await asyncio.wait_for(client.stop_notify(rx), 3)
            except Exception as error:
                recorder.event("disconnect_cleanup_warning", message=str(error))


async def connect_and_record(args, recorder: Recorder) -> None:
    client_class, _ = bluetooth_library()
    matches = await scan_devices(args.scan_seconds)
    selected = next((device for device, info in matches if info["id"] == args.device), None)
    if selected is None:
        raise RuntimeError("Selected AWEAR device was not found. Keep it nearby and disconnect its mobile app, then scan again.")
    recorder.status["state"] = "connecting"
    recorder.publish()
    async with client_class(selected, timeout=30) as client:
        await record_stream(client, recorder, args.seconds, stop_file=args.stop_file)


async def record_until_stopped(args, recorder: Recorder) -> None:
    task = asyncio.create_task(connect_and_record(args, recorder))
    try:
        while not task.done():
            if args.stop_file and args.stop_file.exists():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise RecordingStopped()
            await asyncio.wait({task}, timeout=.2)
        await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Direct AWEAR Bluetooth recording on this computer")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="List nearby AWEAR candidates; do not connect")
    scan.add_argument("--seconds", type=int, default=10, choices=range(2, 31), metavar="2–30")
    scan.add_argument("--result-file", type=Path, help=argparse.SUPPRESS)
    record = commands.add_parser("record", help="Connect to one explicitly selected device and save raw data")
    record.add_argument("--device", required=True, help="Exact device ID from scan")
    record.add_argument("--seconds", type=int, default=60)
    record.add_argument("--scan-seconds", type=int, default=10)
    record.add_argument("--output", type=Path, required=True, help="New session directory")
    record.add_argument("--participant", default="")
    record.add_argument("--stop-file", type=Path, help=argparse.SUPPRESS)
    record.add_argument("--placement", choices=("right", "left", "unknown"), default="unknown",
                        help="Observed wear location; records metadata without changing device settings")
    args = parser.parse_args()
    if args.command == "scan":
        def scan_result(value: dict) -> None:
            if args.result_file:
                atomic_json(args.result_file, value)
            else:
                print(json.dumps(value))
        try:
            devices = asyncio.run(scan_devices(args.seconds))
            scan_result({"devices": [info for _, info in devices]})
        except KeyboardInterrupt:
            scan_result({"error": "Bluetooth scan cancelled"})
            raise SystemExit(130)
        except Exception as error:
            scan_result({"error": str(error)})
            raise SystemExit(1)
        return
    if not 1 <= args.seconds <= 3600 or not 2 <= args.scan_seconds <= 30:
        parser.error("Recording duration must be 1–3600 seconds and scan duration 2–30 seconds")
    recorder = Recorder(args.output, args.device, args.participant, args.placement)
    try:
        asyncio.run(record_until_stopped(args, recorder))
    except (KeyboardInterrupt, RecordingStopped):
        recorder.finish("stopped")
    except Exception as error:
        recorder.finish("error", str(error))
        print(f"Bluetooth recording stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
    else:
        recorder.finish("completed")
    print(f"Bluetooth session saved to {args.output}")


if __name__ == "__main__":
    main()
