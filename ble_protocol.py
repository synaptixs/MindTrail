"""AWEAR LUCA wire-format reader, implemented from the public protocol description.

Reference: https://github.com/eugenehp/awear-rs/tree/df7890471ef1dadc0d47fe0164adc8f702a2b3b6
These protocol facts are community documented, not a vendor compatibility promise.
Raw ADC values are preserved: no offset, filtering, or voltage conversion is applied.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import asdict, dataclass

REFERENCE_REVISION = "df7890471ef1dadc0d47fe0164adc8f702a2b3b6"
SERVICE_UUID = "fc740001-0291-41fc-a9b0-45951b5b01d7"
TX_UUID = "fc740002-0291-41fc-a9b0-45951b5b01d7"
RX_UUID = "fc740003-0291-41fc-a9b0-45951b5b01d7"
# Public device-protocol constant, unrelated to the user's cloud API key.
_AUTH_KEY = bytes.fromhex("4ee1fbef8798e94a17bb945898c1898f")
FORMATS = {1, 2, 3, 4, 7, 8}
EEG_FORMATS = {1, 7, 8}


class ProtocolError(ValueError):
    pass


def challenge_reply(challenge: str) -> bytes:
    if not re.fullmatch(r"[0-9a-fA-F]{8}", challenge):
        raise ProtocolError("Unsupported Bluetooth authentication challenge")
    message = bytes.fromhex(challenge) + struct.pack("<I", 0xD4C3B2A1)
    digest = hmac.new(_AUTH_KEY, message, hashlib.sha256).digest()[:8].hex().upper()
    return ("CRPL:" + digest).encode("ascii")


def ready_info(text: str, reply: bytes | None) -> dict:
    fields = text.removeprefix("AWEAR_READY:").split(":")
    if not text.startswith("AWEAR_READY:") or reply is None or fields[0] != reply.decode()[5:]:
        raise ProtocolError("Bluetooth ready message did not match this connection's authentication")

    def version(index: int) -> str | None:
        if index >= len(fields) or not fields[index].isdigit():
            return None
        number = int(fields[index])
        return f"{number >> 24}.{(number >> 16) & 255}.{number & 65535}"

    return {"board": fields[1] if len(fields) > 1 else None, "firmware": version(2),
            "partSize": int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else None,
            "bootloader": version(4)}


@dataclass(frozen=True)
class Header:
    format: int
    counter: int
    device_id: int
    battery_mv: int
    rssi: int
    replay: bool
    timestamp: int
    last_part_size: int
    total_parts: int

    def metadata(self) -> dict:
        result = asdict(self)
        # JSON numbers cannot exactly represent every 64-bit device identifier.
        result["device_id"] = f"{self.device_id:016X}"
        return result


@dataclass(frozen=True)
class Message:
    header: Header
    payload: bytes


class Assembler:
    """Bounded notification reassembly; damaged blocks are never padded or joined."""

    def __init__(self):
        self.header: Header | None = None
        self.parts: list[bytes] = []
        self.size = 0

    def reset(self) -> None:
        self.header, self.parts, self.size = None, [], 0

    def feed(self, data: bytes) -> tuple[Message | None, list[str]]:
        errors = []
        if len(data) == 36 and data.startswith(b"LUCA"):
            if self.header is not None:
                errors.append("Incomplete message discarded at next header")
            self.reset()
            format_id = struct.unpack_from(">I", data, 4)[0]
            counter, device_id, battery, rssi, replay, timestamp, last, parts = struct.unpack_from("<IQHhIIHH", data, 8)
            if format_id not in FORMATS or replay not in (0, 1) or parts > 128 or last > 2048:
                return None, errors + ["Unsupported or malformed LUCA header"]
            header = Header(format_id, counter, device_id, battery, rssi, bool(replay), timestamp, last, parts)
            if not parts:
                return Message(header, b""), errors
            self.header = header
            return None, errors
        if self.header is None:
            raise ProtocolError("Notification without a supported LUCA header")
        if len(data) > 2048 or self.size + len(data) > 16384:
            self.reset()
            return None, ["Message exceeded size limit"]
        self.parts.append(data)
        self.size += len(data)
        if len(self.parts) < self.header.total_parts:
            return None, errors
        header = self.header
        payload = b"".join(self.parts)
        self.reset()
        if header.last_part_size and len(data) != header.last_part_size:
            return None, ["Final notification size disagreed with LUCA header"]
        return Message(header, payload), errors


def decode_samples(message: Message) -> list[int]:
    payload, format_id = message.payload, message.header.format
    try:
        if format_id == 1:
            payload = bytes.fromhex(payload.decode("ascii").strip())
            if len(payload) != 512:
                raise ProtocolError("Hex EEG block did not contain 256 samples")
            return list(struct.unpack(">256h", payload))
        if format_id == 7:
            if len(payload) != 512:
                raise ProtocolError("Raw EEG block did not contain 256 samples")
            return list(struct.unpack("<256h", payload))
        if format_id == 8:
            result, value, index = [], 0, 0
            while index < len(payload):
                first = payload[index]
                index += 1
                if first & 128:
                    if index == len(payload):
                        raise ProtocolError("Truncated absolute sample in delta EEG")
                    value = ((first & 127) << 8) | payload[index]
                    index += 1
                else:
                    value += first if first < 64 else first - 128
                if not -32768 <= value <= 32767 or len(result) >= 256:
                    raise ProtocolError("Delta EEG sample count or value out of range")
                result.append(value)
            if len(result) != 256:
                raise ProtocolError("Delta EEG block did not contain 256 samples")
            return result
    except (UnicodeError, ValueError, struct.error) as error:
        raise ProtocolError(str(error)) from error
    raise ProtocolError("Message is not a supported EEG format")


class CounterTracker:
    def __init__(self):
        self.previous: dict[str, int] = {}

    def check(self, header: Header) -> dict:
        # Live and saved-memory EEG share a transmission counter on observed
        # firmware. Splitting by replay flag invents gaps at each transition.
        group = f"{header.device_id}:{'raw' if header.format in (7, 8) else 'hex'}"
        previous = self.previous.get(group)
        self.previous[group] = header.counter
        difference = (header.counter - previous) & 0xFFFFFFFF if previous is not None else 1
        return {"missingBefore": difference-1 if 1 < difference < 0x80000000 else 0,
                "duplicateCounter": difference == 0, "counterReset": difference >= 0x80000000}
