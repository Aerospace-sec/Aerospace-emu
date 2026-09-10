"""Contained ARINC 429 security laboratory model.

This module models the parts needed by the report:

* the 32-bit word layout and odd parity;
* a legacy receiver that has no source authentication or freshness state;
* an ingress gateway with explicit security controls;
* deterministic wire serialization for an availability experiment.

It is intentionally a logical simulator. It does not open serial devices,
network sockets, aircraft interfaces, or radio equipment.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Iterable, Mapping
import hashlib
import hmac


WORD_MASK = 0xFFFFFFFF
LABEL_MASK = 0xFF
SDI_MASK = 0x03
DATA_MASK = 0x7FFFF
SSM_MASK = 0x03
PARITY_BIT = 31


class Arinc429Error(ValueError):
    """Raised when a field cannot be represented in an ARINC 429 word."""


def _check_field(name: str, value: int, maximum: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise Arinc429Error(f"{name} must be an integer in [0, {maximum}], got {value!r}")


@dataclass(frozen=True)
class DecodedWord:
    raw: int
    label: int
    sdi: int
    data: int
    ssm: int
    parity_ok: bool


def encode_word(label: int, sdi: int, data: int, ssm: int) -> int:
    """Encode a word using the simulator's numeric bit-position convention.

    The label occupies bits 1-8, SDI bits 9-10, data bits 11-29, SSM bits
    30-31, and bit 32 is odd parity. ARINC 429 transmits the label bit order
    in a defined order on the wire; this simulator keeps the label numeric
    representation stable and documents that convention in the report.
    """

    _check_field("label", label, LABEL_MASK)
    _check_field("sdi", sdi, SDI_MASK)
    _check_field("data", data, DATA_MASK)
    _check_field("ssm", ssm, SSM_MASK)

    raw = label | (sdi << 8) | (data << 10) | (ssm << 29)
    # Odd parity over all 32 bits: set the parity bit when the lower 31 bits
    # currently contain an even number of one bits.
    if raw.bit_count() % 2 == 0:
        raw |= 1 << PARITY_BIT
    return raw


def decode_word(raw: int) -> DecodedWord:
    """Decode a 32-bit word without discarding parity-invalid observations."""

    _check_field("raw", raw, WORD_MASK)
    return DecodedWord(
        raw=raw,
        label=raw & LABEL_MASK,
        sdi=(raw >> 8) & SDI_MASK,
        data=(raw >> 10) & DATA_MASK,
        ssm=(raw >> 29) & SSM_MASK,
        parity_ok=raw.bit_count() % 2 == 1,
    )


def tamper_data(raw: int, data: int) -> int:
    """Change only the data field while generating a valid replacement word."""

    decoded = decode_word(raw)
    return encode_word(decoded.label, decoded.sdi, data, decoded.ssm)


@dataclass(frozen=True)
class BusFrame:
    timestamp_ms: int
    source: str
    raw_word: int
    sequence: int | None = None
    tag: str | None = None

    @property
    def decoded(self) -> DecodedWord:
        return decode_word(self.raw_word)


@dataclass(frozen=True)
class Admission:
    accepted: bool
    reason: str
    frame: BusFrame


def _mac_input(frame: BusFrame) -> bytes:
    sequence = "-" if frame.sequence is None else str(frame.sequence)
    return f"{frame.source}|{frame.timestamp_ms}|{sequence}|{frame.raw_word:08x}".encode()


def make_secure_frame(
    timestamp_ms: int,
    source: str,
    raw_word: int,
    sequence: int,
    secret: bytes,
) -> BusFrame:
    """Create a lab-only authenticated wrapper around a native word."""

    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must be non-negative")
    if sequence < 0:
        raise ValueError("sequence must be non-negative")
    unsigned = BusFrame(timestamp_ms, source, raw_word, sequence, None)
    tag = hmac.new(secret, _mac_input(unsigned), hashlib.sha256).hexdigest()
    return replace(unsigned, tag=tag)


class LegacyReceiver:
    """Minimal receiver behavior: parity plus a configured label allowlist."""

    def __init__(self, accepted_labels: Iterable[int]) -> None:
        self.accepted_labels = frozenset(accepted_labels)

    def receive(self, frame: BusFrame) -> Admission:
        decoded = frame.decoded
        if not decoded.parity_ok:
            return Admission(False, "parity", frame)
        if decoded.label not in self.accepted_labels:
            return Admission(False, "unknown_label", frame)
        # The native word has no source identity or freshness field.
        return Admission(True, "accepted", frame)


@dataclass(frozen=True)
class LabelPolicy:
    minimum: int
    maximum: int
    max_delta: int | None = None


class SecureGateway:
    """Admission control for a managed, security-terminating ingress.

    The MAC, sequence and timestamp are an overlay around the native ARINC
    word. They are not claimed to be fields in the ARINC 429 standard word.
    """

    def __init__(
        self,
        secret: bytes | Mapping[str, bytes],
        authorized_sources: Iterable[str],
        policies: Mapping[int, LabelPolicy],
        *,
        max_age_ms: int = 100,
        max_future_ms: int = 20,
        max_frames_per_window: int = 10,
        window_ms: int = 1000,
    ) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        if max_age_ms < 0 or max_future_ms < 0:
            raise ValueError("time bounds must be non-negative")
        if max_frames_per_window <= 0 or window_ms <= 0:
            raise ValueError("rate limits must be positive")
        if isinstance(secret, bytes):
            # Backward-compatible single-source lab mode. Multi-source
            # deployments should pass a per-source key mapping.
            self.secrets = {"*": secret}
        else:
            self.secrets = dict(secret)
            if any(not source or not key for source, key in self.secrets.items()):
                raise ValueError("each source must have a non-empty key")
        self.authorized_sources = frozenset(authorized_sources)
        self.policies = dict(policies)
        self.max_age_ms = max_age_ms
        self.max_future_ms = max_future_ms
        self.max_frames_per_window = max_frames_per_window
        self.window_ms = window_ms
        self._last_sequence: dict[tuple[str, int, int], int] = {}
        self._last_data: dict[tuple[str, int, int], int] = {}
        self._accepted_timestamps: deque[int] = deque()

    def _rate_limited(self, now_ms: int) -> bool:
        cutoff = now_ms - self.window_ms
        while self._accepted_timestamps and self._accepted_timestamps[0] <= cutoff:
            self._accepted_timestamps.popleft()
        return len(self._accepted_timestamps) >= self.max_frames_per_window

    def admit(self, frame: BusFrame, now_ms: int) -> Admission:
        decoded = frame.decoded
        if not decoded.parity_ok:
            return Admission(False, "parity", frame)
        if frame.source not in self.authorized_sources:
            return Admission(False, "unauthorized_source", frame)
        if frame.sequence is None or frame.tag is None:
            return Admission(False, "missing_authentication", frame)

        secret = self.secrets.get(frame.source, self.secrets.get("*"))
        if secret is None:
            return Admission(False, "source_key", frame)
        unsigned = replace(frame, tag=None)
        expected_tag = hmac.new(secret, _mac_input(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(frame.tag, expected_tag):
            return Admission(False, "authentication", frame)

        age = now_ms - frame.timestamp_ms
        if age > self.max_age_ms:
            return Admission(False, "stale", frame)
        if age < -self.max_future_ms:
            return Admission(False, "future_timestamp", frame)

        key = (frame.source, decoded.label, decoded.sdi)
        previous_sequence = self._last_sequence.get(key)
        if previous_sequence is not None and frame.sequence <= previous_sequence:
            return Admission(False, "replay", frame)

        policy = self.policies.get(decoded.label)
        if policy is None:
            return Admission(False, "unknown_label", frame)
        if not policy.minimum <= decoded.data <= policy.maximum:
            return Admission(False, "semantic_range", frame)

        previous_data = self._last_data.get(key)
        if policy.max_delta is not None and previous_data is not None:
            if abs(decoded.data - previous_data) > policy.max_delta:
                return Admission(False, "semantic_delta", frame)

        if self._rate_limited(now_ms):
            return Admission(False, "rate_limit", frame)

        self._last_sequence[key] = frame.sequence
        self._last_data[key] = decoded.data
        self._accepted_timestamps.append(now_ms)
        return Admission(True, "accepted", frame)


@dataclass(frozen=True)
class WireTransmission:
    frame: BusFrame
    start_ms: float
    end_ms: float


def serialize_frames(frames: Iterable[BusFrame], bit_rate: int = 100_000) -> list[WireTransmission]:
    """Serialize queued words in arrival order for an availability estimate."""

    if bit_rate not in (12_500, 100_000):
        raise ValueError("bit_rate must be 12500 or 100000")
    duration_ms = 32_000.0 / bit_rate
    cursor = 0.0
    transmissions: list[WireTransmission] = []
    for frame in sorted(enumerate(frames), key=lambda item: (item[1].timestamp_ms, item[0])):
        item = frame[1]
        start = max(cursor, float(item.timestamp_ms))
        end = start + duration_ms
        transmissions.append(WireTransmission(item, start, end))
        cursor = end
    return transmissions


def timing_lock(expected_bit_rate: int, actual_bit_rate: int, tolerance: float = 0.02) -> bool:
    """Return whether a configured receiver can treat a rate as locked.

    This is a deliberately conservative digital abstraction for the two
    nominal ARINC 429 modes. It is not an electrical tolerance model for a
    particular LRU; hardware bench measurements remain necessary.
    """

    if expected_bit_rate not in (12_500, 100_000):
        raise ValueError("expected_bit_rate must be 12500 or 100000")
    if actual_bit_rate not in (12_500, 100_000):
        raise ValueError("actual_bit_rate must be 12500 or 100000")
    return abs(actual_bit_rate / expected_bit_rate - 1.0) <= tolerance


TEST_LABEL = 0o203
AUTHORIZED_SOURCE = "air_data_computer"
LAB_SECRET = b"arinc429-lab-secret"
SAFE_DATA = 1000
FORGED_DATA = 400_000
SEMANTIC_LIMIT = 10_000


def _gateway() -> SecureGateway:
    return SecureGateway(
        {AUTHORIZED_SOURCE: LAB_SECRET},
        {AUTHORIZED_SOURCE},
        {TEST_LABEL: LabelPolicy(0, SEMANTIC_LIMIT, max_delta=2_000)},
        max_age_ms=100,
        max_future_ms=20,
        max_frames_per_window=10,
    )


def run_scenarios() -> dict[str, object]:
    """Run deterministic before/after experiments used by the report."""

    legacy = LegacyReceiver({TEST_LABEL})
    native = encode_word(TEST_LABEL, 0, SAFE_DATA, 0)
    forged = encode_word(TEST_LABEL, 0, FORGED_DATA, 0)

    injection_frame = BusFrame(20, "attacker", forged)
    injection_legacy = legacy.receive(injection_frame)
    injection_secure = _gateway().admit(injection_frame, 20)

    replay_frame = BusFrame(0, AUTHORIZED_SOURCE, native)
    replay_legacy = legacy.receive(BusFrame(500, "attacker", native))
    secure_replay = make_secure_frame(0, AUTHORIZED_SOURCE, native, 1, LAB_SECRET)
    replay_gateway = _gateway()
    first_delivery = replay_gateway.admit(secure_replay, 0)
    replay_secure = replay_gateway.admit(secure_replay, 20)
    stale_secure = _gateway().admit(secure_replay, 500)

    tampered_native = tamper_data(native, SAFE_DATA + 1_000)
    tampered_frame = BusFrame(20, AUTHORIZED_SOURCE, tampered_native)
    tamper_legacy = legacy.receive(tampered_frame)
    authenticated_original = make_secure_frame(20, AUTHORIZED_SOURCE, native, 2, LAB_SECRET)
    authenticated_tamper = replace(authenticated_original, raw_word=tampered_native)
    tamper_secure = _gateway().admit(authenticated_tamper, 20)

    semantic_native = encode_word(TEST_LABEL, 0, SEMANTIC_LIMIT + 1, 0)
    semantic_frame = BusFrame(20, AUTHORIZED_SOURCE, semantic_native)
    semantic_legacy = legacy.receive(semantic_frame)
    semantic_secure_frame = make_secure_frame(20, AUTHORIZED_SOURCE, semantic_native, 3, LAB_SECRET)
    semantic_secure = _gateway().admit(semantic_secure_frame, 20)

    flood_frames = [
        BusFrame(0, "attacker", encode_word(TEST_LABEL, 0, 2_000 + index, 0))
        for index in range(64)
    ]
    legitimate_flood_frame = BusFrame(0, AUTHORIZED_SOURCE, native)
    legacy_flood_delivery = [legacy.receive(frame) for frame in flood_frames + [legitimate_flood_frame]]
    legacy_wire = serialize_frames(flood_frames + [legitimate_flood_frame], 100_000)
    legacy_legitimate_wire = next(
        transmission for transmission in legacy_wire if transmission.frame is legitimate_flood_frame
    )

    flood_gateway = _gateway()
    secure_legitimate = make_secure_frame(0, AUTHORIZED_SOURCE, native, 1, LAB_SECRET)
    admitted_flood = [
        flood_gateway.admit(frame, 0) for frame in flood_frames
    ]
    admitted_legitimate = flood_gateway.admit(secure_legitimate, 0)
    hardened_wire = serialize_frames(
        [secure_legitimate] if admitted_legitimate.accepted else [],
        100_000,
    )
    hardened_legitimate_wire = hardened_wire[0]

    passive_capture = decode_word(native)
    single_bit_error = decode_word(native ^ (1 << 12))
    expected_rate = 100_000
    actual_rate = 12_500
    rate_mismatch = {
        "expected_bit_rate": expected_rate,
        "actual_bit_rate": actual_rate,
        "expected_word_duration_ms": 32_000 / expected_rate,
        "actual_word_duration_ms": 32_000 / actual_rate,
        "receiver_valid_word": timing_lock(expected_rate, actual_rate),
        "reason": "configured_rate_mismatch",
    }

    return {
        "model": {
            "test_label_octal": oct(TEST_LABEL),
            "bit_rates_bps": [12_500, 100_000],
            "word_bits": 32,
            "semantic_limit": SEMANTIC_LIMIT,
        },
        "injection": {
            "legacy": {"accepted": injection_legacy.accepted, "reason": injection_legacy.reason},
            "hardened": {"accepted": injection_secure.accepted, "reason": injection_secure.reason},
        },
        "replay": {
            "legacy": {"accepted": replay_legacy.accepted, "reason": replay_legacy.reason},
            "first_secure_delivery": {"accepted": first_delivery.accepted, "reason": first_delivery.reason},
            "immediate_replay": {"accepted": replay_secure.accepted, "reason": replay_secure.reason},
            "stale_replay": {"accepted": stale_secure.accepted, "reason": stale_secure.reason},
            "replayed_raw": f"0x{replay_frame.raw_word:08x}",
        },
        "parity_recomputed_tamper": {
            "parity_valid_after_tamper": decode_word(tampered_native).parity_ok,
            "legacy": {"accepted": tamper_legacy.accepted, "reason": tamper_legacy.reason},
            "hardened": {"accepted": tamper_secure.accepted, "reason": tamper_secure.reason},
        },
        "semantic_range": {
            "legacy": {"accepted": semantic_legacy.accepted, "reason": semantic_legacy.reason},
            "hardened": {"accepted": semantic_secure.accepted, "reason": semantic_secure.reason},
        },
        "flood": {
            "attacker_frames": len(flood_frames),
            "legacy_attacker_frames_accepted": sum(
                result.accepted for result in legacy_flood_delivery[:-1]
            ),
            "legacy_legitimate_start_ms": legacy_legitimate_wire.start_ms,
            "hardened_attacker_frames_rejected": sum(not result.accepted for result in admitted_flood),
            "hardened_legitimate_admitted": admitted_legitimate.accepted,
            "hardened_legitimate_start_ms": hardened_legitimate_wire.start_ms,
            "deadline_ms": 5.0,
            "legacy_deadline_missed": legacy_legitimate_wire.start_ms > 5.0,
            "hardened_deadline_missed": hardened_legitimate_wire.start_ms > 5.0,
        },
        "passive_observation": {
            "captured_label_octal": oct(passive_capture.label),
            "captured_data": passive_capture.data,
            "native_confidentiality": False,
        },
        "parity_baseline": {
            "single_bit_flip_parity_ok": single_bit_error.parity_ok,
            "single_bit_flip_legacy_reason": legacy.receive(
                BusFrame(20, "attacker", native ^ (1 << 12))
            ).reason,
        },
        "rate_mismatch": rate_mismatch,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_scenarios(), ensure_ascii=False, indent=2, sort_keys=True))
