import asyncio
import csv
import json
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ble_protocol as protocol
import bluetooth_collector as collector
from bluetooth_manager import BluetoothManager


def wire_header(format_id=7, counter=1, timestamp=1_760_000_000, parts=1, last=512, replay=False):
    data = bytearray(36)
    data[:4] = b"LUCA"
    data[4:8] = format_id.to_bytes(4, "big")
    data[8:12] = counter.to_bytes(4, "little")
    data[12:20] = (0xFEDCBA9876543210).to_bytes(8, "little")
    data[20:22] = (3900).to_bytes(2, "little")
    data[22:24] = (-54).to_bytes(2, "little", signed=True)
    data[24:28] = int(replay).to_bytes(4, "little")
    data[28:32] = timestamp.to_bytes(4, "little")
    data[32:34] = last.to_bytes(2, "little")
    data[34:36] = parts.to_bytes(2, "little")
    return bytes(data)


class ProtocolTests(unittest.TestCase):
    def test_header_and_fragmented_raw_values_are_exact(self):
        samples = [7350+index for index in range(256)]
        payload = struct.pack("<256h", *samples)
        decoder = protocol.Assembler()
        self.assertIsNone(decoder.feed(wire_header(parts=3, last=24))[0])
        self.assertIsNone(decoder.feed(payload[:244])[0])
        self.assertIsNone(decoder.feed(payload[244:488])[0])
        message, errors = decoder.feed(payload[488:])
        self.assertEqual(errors, [])
        self.assertEqual(protocol.decode_samples(message), samples)
        self.assertEqual(message.header.timestamp, 1_760_000_000)
        self.assertEqual(message.header.counter, 1)
        self.assertEqual(message.header.rssi, -54)
        self.assertEqual(message.header.metadata()["device_id"], "FEDCBA9876543210")

    def test_hex_delta_and_malformed_samples(self):
        samples = [7350+(index % 2) for index in range(256)]
        # Use the wire parser, rather than constructing private decoder state.
        decoder = protocol.Assembler()
        decoder.feed(wire_header(format_id=1, last=1024))
        message, _ = decoder.feed(struct.pack(">256h", *samples).hex().encode())
        self.assertEqual(protocol.decode_samples(message), samples)
        delta = bytes([0x80 | (7350 >> 8), 7350 & 255]) + bytes(1 if index % 2 else 127 for index in range(1,256))
        decoder.feed(wire_header(format_id=8, last=len(delta)))
        message, _ = decoder.feed(delta)
        self.assertEqual(protocol.decode_samples(message), samples)
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_samples(protocol.Message(message.header, delta[:-1]))
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_samples(protocol.Message(message.header, b"\x80"))

    def test_missing_notification_resynchronizes_and_counter_wrap(self):
        decoder = protocol.Assembler()
        decoder.feed(wire_header(parts=2, last=1))
        decoder.feed(b"first part")
        _, errors = decoder.feed(wire_header(counter=4, last=512))
        self.assertTrue(errors)
        message, _ = decoder.feed(bytes(512))
        self.assertEqual(len(protocol.decode_samples(message)), 256)
        decoder.feed(wire_header(last=20))
        self.assertTrue(decoder.feed(b"wrong")[1])
        tracker = protocol.CounterTracker()
        def header(counter):
            decoder.feed(wire_header(counter=counter))
            return decoder.feed(bytes(512))[0].header
        tracker.check(header(0xFFFFFFFF))
        self.assertEqual(tracker.check(header(0))["missingBefore"], 0)
        self.assertEqual(tracker.check(header(3))["missingBefore"], 2)
        self.assertTrue(tracker.check(header(3))["duplicateCounter"])

    def test_live_and_replayed_eeg_share_transmission_counter(self):
        tracker = protocol.CounterTracker()
        decoder = protocol.Assembler()
        for counter, replay in [(1, False), (2, True), (3, True), (4, False)]:
            decoder.feed(wire_header(counter=counter, replay=replay))
            message, _ = decoder.feed(bytes(512))
            tracking = tracker.check(message.header)
            self.assertEqual(tracking["missingBefore"], 0)
            self.assertFalse(tracking["counterReset"])


class Peer:
    def __init__(self, wrong_ready=False):
        self.is_connected = True
        self.writes = []
        self.wrong_ready = wrong_ready
        self.callback = None
        self.services = SimpleNamespace(get_characteristic=lambda uuid: SimpleNamespace(
            properties=["notify"] if uuid == protocol.RX_UUID else ["write-without-response"]))

    def message(self, format_id, payload, counter=1, replay=False):
        self.callback(None, wire_header(format_id=format_id, last=len(payload), counter=counter, replay=replay))
        self.callback(None, payload)

    async def start_notify(self, characteristic, callback):
        self.callback = callback
        self.message(2, b"AWEAR_CONNECTED:12345678")

    async def stop_notify(self, characteristic):
        pass

    async def write_gatt_char(self, characteristic, data, response):
        self.writes.append(data)
        if data.startswith(b"CRPL:"):
            echo = b"wrong" if self.wrong_ready else data[5:]
            self.message(2, b"AWEAR_READY:"+echo+b":A:16777216:244:16777216")
        elif data == b"START":
            self.message(7, struct.pack("<256h", *([7350]*256)))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_file_cancels_pending_connection_and_runs_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            stop_file = Path(folder) / "stop"
            started, cleaned = asyncio.Event(), asyncio.Event()
            async def pending_connection(args, recorder):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            args = SimpleNamespace(stop_file=stop_file)
            with patch.object(collector, "connect_and_record", pending_connection):
                task = asyncio.create_task(collector.record_until_stopped(args, None))
                await asyncio.wait_for(started.wait(), 1)
                stop_file.touch()
                with self.assertRaises(collector.RecordingStopped):
                    await asyncio.wait_for(task, 1)
            self.assertTrue(cleaned.is_set())


    async def test_auth_stream_and_stop_preserve_raw_without_clock_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = collector.Recorder(Path(folder), "MAC-UUID", "P-TEST", placement="left")
            peer = Peer()
            try:
                # Allow Windows CI time to flush the scripted notifications to disk.
                await collector.record_stream(peer, recorder, seconds=1)
            finally:
                recorder.finish("completed")
            self.assertEqual(peer.writes, [protocol.challenge_reply("12345678"), b"START", b"STOP"])
            row = json.loads((Path(folder)/"eeg.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["samples"], [7350]*256)
            self.assertEqual(row["header"]["device_id"], "FEDCBA9876543210")
            self.assertEqual(recorder.status["eegBlocks"], 1)
            with (Path(folder)/"eeg.csv").open() as stream:
                csv_row = next(csv.DictReader(stream))
            self.assertEqual(csv_row["sample_000"], "7350")
            self.assertFalse(json.loads((Path(folder)/"session.json").read_text())["clockChanged"])
            self.assertEqual(json.loads((Path(folder)/"session.json").read_text())["placement"], "left")

    async def test_wrong_ready_does_not_start_streaming(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = collector.Recorder(Path(folder), "MAC-UUID")
            peer = Peer(wrong_ready=True)
            with self.assertRaisesRegex(RuntimeError, "authentication"):
                await collector.record_stream(peer, recorder, seconds=.02)
            recorder.finish("error")
            self.assertEqual(len(peer.writes), 1)
            self.assertEqual(recorder.status["eegBlocks"], 0)

    async def test_replay_keeps_unknown_placement_and_reports_device_deletion(self):
        class ReplayPeer(Peer):
            async def write_gatt_char(self, characteristic, data, response):
                await super().write_gatt_char(characteristic, data, response)
                if data == b"START":
                    self.message(7, struct.pack("<256h", *([7350]*256)), counter=2, replay=True)
                    self.message(7, struct.pack("<256h", *([7350]*256)), counter=3)
                    self.message(3, b"Saved EEG data deleted: 512 bytes")
        with tempfile.TemporaryDirectory() as folder:
            completed=asyncio.Event()
            class ReplayRecorder(collector.Recorder):
                def received(self,*args):
                    # Exercise slow notification persistence without a 20 ms throughput assumption.
                    time.sleep(.01)
                    super().received(*args)
                def publish(self):
                    super().publish()
                    if self.status.get("savedMemoryDeletionReported"):completed.set()
            recorder = ReplayRecorder(Path(folder), "MAC-UUID", placement="left")
            task=asyncio.create_task(collector.record_stream(ReplayPeer(),recorder,seconds=10))
            try:
                await asyncio.wait_for(completed.wait(),3)
            finally:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
                recorder.finish("completed")
            rows = [json.loads(line) for line in (Path(folder)/"eeg.jsonl").read_text().splitlines()]
            self.assertEqual([row["placement"] for row in rows], ["left", "unknown", "left"])
            self.assertEqual(recorder.status["liveBlocks"], 2)
            self.assertEqual(recorder.status["replayedBlocks"], 1)
            self.assertEqual(recorder.status["missingBlocks"], 0)
            self.assertTrue(recorder.status["savedMemoryDeletionReported"])

    async def test_disconnection_retains_partial_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = Peer()
            class DisconnectRecorder(collector.Recorder):
                def eeg(self, *args):
                    super().eeg(*args)
                    peer.is_connected = False
            recorder = DisconnectRecorder(Path(folder), "MAC-UUID")
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await collector.record_stream(peer, recorder, seconds=1)
            recorder.finish("error")
            self.assertEqual(recorder.status["eegBlocks"], 1)
            self.assertTrue((Path(folder)/"raw-notifications.jsonl").stat().st_size)

    async def test_connection_without_eeg_is_an_error(self):
        class SilentPeer(Peer):
            async def write_gatt_char(self, characteristic, data, response):
                if data == b"START":
                    self.writes.append(data)
                else:
                    await super().write_gatt_char(characteristic, data, response)
        with tempfile.TemporaryDirectory() as folder:
            recorder = collector.Recorder(Path(folder), "MAC-UUID")
            peer = SilentPeer()
            with self.assertRaisesRegex(RuntimeError, "no valid"):
                await collector.record_stream(peer, recorder, seconds=.02)
            recorder.finish("error")
            self.assertEqual(recorder.status["eegBlocks"], 0)
            self.assertEqual(peer.writes[-1], b"STOP")

    async def test_subscription_timeout_closes_session(self):
        class StalledPeer(Peer):
            async def start_notify(self, characteristic, callback):
                await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as folder:
            recorder = collector.Recorder(Path(folder), "MAC-UUID")
            with self.assertRaisesRegex(RuntimeError, "subscription timed out"):
                await collector.record_stream(StalledPeer(), recorder, seconds=1, ready_timeout=.01)
            recorder.finish("error")


class CaptureFileTests(unittest.TestCase):
    def test_existing_capture_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "eeg.csv").write_text("previous capture")
            with self.assertRaisesRegex(ValueError, "new output directory"):
                collector.Recorder(output, "MAC-UUID")
            self.assertEqual((output / "eeg.csv").read_text(), "previous capture")
            self.assertFalse((output / "raw-notifications.jsonl").exists())


class ManagerTests(unittest.TestCase):
    def test_explicit_selection_and_download_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = BluetoothManager(Path(temporary))
            manager.python.parent.mkdir(parents=True)
            manager.python.touch()
            manager.devices = [{"id": "selected-device"}]
            with self.assertRaises(ValueError):
                manager.start("different-device", 60, "P-TEST")
            with self.assertRaisesRegex(ValueError, "placement"):
                manager.start("selected-device", 60, "P-TEST", "unsupported")
            process = Mock()
            process.poll.return_value = 0
            with patch("bluetooth_manager.subprocess.Popen", return_value=process) as popen:
                session = manager.start("selected-device", 60, "P-TEST", "left")["sessionId"]
                arguments = popen.call_args.args[0]
                self.assertEqual(arguments[arguments.index("--device")+1], "selected-device")
                self.assertEqual(arguments[arguments.index("--placement")+1], "left")
                self.assertNotIn("shell", popen.call_args.kwargs)
            with self.assertRaises(ValueError):
                manager.download_path(session, "../../.env")
            with self.assertRaises(ValueError):
                manager.download_path("../../", "eeg.csv")
            self.assertTrue(manager.download_path(session, "collector.log").is_file())
            process.poll.return_value = None
            with self.assertRaisesRegex(ValueError, "Stop"):
                manager.download_path(session, "collector.log")


if __name__ == "__main__":
    unittest.main()
