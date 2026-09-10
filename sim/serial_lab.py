"""RS-232/RS-422 serial-interface security laboratory.

This module models two civil-aviation deployment boundaries:

* a synthetic RS-422 full-duplex LRU-to-LRU avionics link; and
* a synthetic RS-232 full-duplex ground-maintenance link.

RS-232 and RS-422 describe electrical signaling, not authentication or an
aircraft application protocol. The frame format and command meanings below
are therefore explicitly synthetic project ICD data. The model is digital: it
does not open serial devices and does not claim voltage, impedance, EMI, or
vendor-LRU behavior.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import json
from typing import Any, Iterable, Mapping


SYNC_BYTE = 0x7E
PROTOCOL_VERSION = 1
HEADER_SIZE = 1 + 1 + 1 + 4 + 2
CRC_SIZE = 2
MAX_PAYLOAD_BYTES = 2048

COMMAND_CODES: dict[str, int] = {
    "AIR_DATA_UPDATE": 0x40,
    "NAV_REFERENCE_UPDATE": 0x41,
    "BITE_LOG_READ": 0x11,
    "BITE_LOG_RESPONSE": 0x12,
    "SET_MAINTENANCE_MODE": 0x20,
    "LOAD_CONFIGURATION": 0x30,
    "CLEAR_BITE_LOG": 0x31,
}
CODE_TO_COMMAND = {code: name for name, code in COMMAND_CODES.items()}


class SerialLabError(ValueError):
    """Raised for invalid synthetic serial configuration or framing."""


def _check_u16(name: str, value: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise SerialLabError(f"{name} must be an integer in [0, 65535]")


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """Return CRC-16/CCITT-FALSE for transmission-error detection."""

    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise SerialLabError("payload must be a mapping")
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise SerialLabError("payload must contain JSON-compatible values") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise SerialLabError("payload is too large")
    return encoded


def encode_native_frame(message_type: str, sequence: int, payload: Mapping[str, Any]) -> bytes:
    """Encode the synthetic unauthenticated application frame.

    Layout: ``SYNC | VERSION | TYPE | SEQUENCE(u32) | LENGTH(u16) |
    JSON_PAYLOAD | CRC16``. CRC detects accidental corruption; it does not
    establish source identity, authorization, confidentiality, or freshness.
    """

    if message_type not in COMMAND_CODES:
        raise SerialLabError(f"unknown message type: {message_type}")
    if not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFFFFFF:
        raise SerialLabError("sequence must be an unsigned 32-bit integer")
    encoded_payload = _canonical_payload(payload)
    header = bytes(
        (
            SYNC_BYTE,
            PROTOCOL_VERSION,
            COMMAND_CODES[message_type],
        )
    ) + sequence.to_bytes(4, "big") + len(encoded_payload).to_bytes(2, "big")
    body = header + encoded_payload
    return body + crc16_ccitt(body).to_bytes(2, "big")


@dataclass(frozen=True)
class DecodedSerialFrame:
    raw: bytes
    message_type: str
    sequence: int
    payload: dict[str, Any]
    crc_ok: bool


def decode_native_frame(raw: bytes) -> DecodedSerialFrame:
    """Decode a frame while preserving a CRC-invalid observation."""

    if not isinstance(raw, bytes) or len(raw) < HEADER_SIZE + CRC_SIZE:
        raise SerialLabError("malformed_frame")
    if raw[0] != SYNC_BYTE:
        raise SerialLabError("sync")
    if raw[1] != PROTOCOL_VERSION:
        raise SerialLabError("version")
    message_type = CODE_TO_COMMAND.get(raw[2])
    if message_type is None:
        raise SerialLabError("unknown_message_type")
    sequence = int.from_bytes(raw[3:7], "big")
    payload_length = int.from_bytes(raw[7:9], "big")
    expected_length = HEADER_SIZE + payload_length + CRC_SIZE
    if payload_length > MAX_PAYLOAD_BYTES or len(raw) != expected_length:
        raise SerialLabError("length")
    payload_bytes = raw[HEADER_SIZE : HEADER_SIZE + payload_length]
    try:
        payload = json.loads(payload_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SerialLabError("payload_encoding") from exc
    if not isinstance(payload, dict):
        raise SerialLabError("payload_type")
    expected_crc = crc16_ccitt(raw[:-2])
    received_crc = int.from_bytes(raw[-2:], "big")
    return DecodedSerialFrame(raw, message_type, sequence, payload, expected_crc == received_crc)


def tamper_native_payload(raw: bytes, payload: Mapping[str, Any], sequence: int | None = None) -> bytes:
    """Create a modified frame with a newly computed CRC."""

    decoded = decode_native_frame(raw)
    return encode_native_frame(
        decoded.message_type,
        decoded.sequence if sequence is None else sequence,
        payload,
    )


@dataclass(frozen=True)
class SerialInterfaceProfile:
    """Digital deployment profile for one RS-232 or RS-422 link."""

    name: str
    standard: str
    differential: bool
    full_duplex: bool
    baud_rate: int = 115_200
    data_bits: int = 8
    parity: str = "N"
    stop_bits: int = 1
    independent_signal_pairs: int = 1
    connector_scope: str = "controlled_test_port"
    termination_required: bool = False

    def validate(self) -> None:
        if not self.name or not self.standard:
            raise SerialLabError("interface name and standard must not be empty")
        if self.baud_rate <= 0:
            raise SerialLabError("baud_rate must be positive")
        if self.data_bits not in (7, 8, 9):
            raise SerialLabError("data_bits must be 7, 8, or 9")
        if self.parity not in {"N", "E", "O"}:
            raise SerialLabError("parity must be N, E, or O")
        if self.stop_bits not in (1, 2):
            raise SerialLabError("stop_bits must be 1 or 2")
        if not self.full_duplex:
            raise SerialLabError("this laboratory profile requires full duplex")
        if self.independent_signal_pairs <= 0:
            raise SerialLabError("independent_signal_pairs must be positive")

    @property
    def frame_bits(self) -> int:
        return 1 + self.data_bits + (0 if self.parity == "N" else 1) + self.stop_bits

    @property
    def profile(self) -> dict[str, object]:
        return {
            "name": self.name,
            "standard": self.standard,
            "differential": self.differential,
            "full_duplex": self.full_duplex,
            "baud_rate": self.baud_rate,
            "uart_format": f"{self.data_bits}{self.parity}{self.stop_bits}",
            "frame_bits": self.frame_bits,
            "independent_signal_pairs": self.independent_signal_pairs,
            "connector_scope": self.connector_scope,
            "termination_required": self.termination_required,
            "cryptographic_authentication": False,
            "cryptographic_confidentiality": False,
        }


RS232_MAINTENANCE_PROFILE = SerialInterfaceProfile(
    name="RS232-GSE-MAINT",
    standard="TIA-232-F",
    differential=False,
    full_duplex=True,
    baud_rate=115_200,
    independent_signal_pairs=1,
    connector_scope="ground_maintenance_connector",
)

RS422_AVIONICS_PROFILE = SerialInterfaceProfile(
    name="RS422-AVIONICS-LRU",
    standard="TIA-422-B",
    differential=True,
    full_duplex=True,
    baud_rate=115_200,
    independent_signal_pairs=2,
    connector_scope="aircraft_equipment_interconnect",
    termination_required=True,
)


@dataclass(frozen=True)
class SerialWireEvent:
    link_name: str
    direction: str
    requested_ms: int
    start_ms: float
    end_ms: float
    baud_rate: int
    raw: bytes
    observed: bytes
    fault: str | None = None

    @property
    def wire_duration_ms(self) -> float:
        return self.end_ms - self.start_ms


class SerialLink:
    """Digital full-duplex wire model with independent direction clocks."""

    def __init__(self, profile: SerialInterfaceProfile) -> None:
        profile.validate()
        self.profile = profile
        self._next_start_ms: dict[str, float] = {}
        self.events: list[SerialWireEvent] = []

    def _validate_direction(self, direction: str) -> None:
        if not direction or "->" not in direction:
            raise SerialLabError("direction must use SOURCE->DESTINATION form")
        if direction not in self._next_start_ms:
            self._next_start_ms[direction] = 0.0

    def transmit(
        self,
        raw: bytes,
        *,
        direction: str,
        requested_ms: int,
        bit_flip: tuple[int, int] | None = None,
        drop: bool = False,
    ) -> SerialWireEvent:
        if requested_ms < 0:
            raise SerialLabError("requested_ms must be non-negative")
        if not isinstance(raw, bytes) or not raw:
            raise SerialLabError("raw must be non-empty bytes")
        self._validate_direction(direction)
        observed = bytearray(raw)
        fault = None
        if bit_flip is not None:
            byte_index, bit_index = bit_flip
            if not 0 <= byte_index < len(observed) or not 0 <= bit_index < 8:
                raise SerialLabError("bit_flip is outside the frame")
            observed[byte_index] ^= 1 << bit_index
            fault = f"bit_flip:{byte_index}:{bit_index}"
        if drop:
            fault = "drop" if fault is None else f"{fault},drop"
        bit_time_ms = 1_000.0 / self.profile.baud_rate
        duration_ms = len(raw) * self.profile.frame_bits * bit_time_ms
        start_ms = max(float(requested_ms), self._next_start_ms[direction])
        end_ms = start_ms + duration_ms
        self._next_start_ms[direction] = end_ms
        event = SerialWireEvent(
            link_name=self.profile.name,
            direction=direction,
            requested_ms=requested_ms,
            start_ms=start_ms,
            end_ms=end_ms,
            baud_rate=self.profile.baud_rate,
            raw=raw,
            observed=bytes(observed),
            fault=fault,
        )
        self.events.append(event)
        return event

    def capture(self, direction: str | None = None) -> list[SerialWireEvent]:
        """Return observations available to a passive tap."""

        if direction is None:
            return list(self.events)
        return [event for event in self.events if event.direction == direction]


@dataclass(frozen=True)
class SerialMessagePolicy:
    message_type: str
    maintenance_only: bool = False
    roles: frozenset[str] = frozenset()
    maximum_payload_bytes: int = MAX_PAYLOAD_BYTES


@dataclass(frozen=True)
class SerialAdmission:
    accepted: bool
    reason: str
    decoded: DecodedSerialFrame | None
    effect: str


class SerialReceiver:
    """Native receiver with framing/CRC checks but no cryptographic trust."""

    def __init__(
        self,
        *,
        name: str,
        accepted_types: Iterable[str],
        maintenance_port_enabled: bool = True,
    ) -> None:
        self.name = name
        self.accepted_types = frozenset(accepted_types)
        self.maintenance_port_enabled = maintenance_port_enabled
        self.received: list[SerialAdmission] = []
        self.state: dict[str, Any] = {
            "maintenance_mode": False,
            "calibration_id": "BASELINE",
            "calibration_offset_ft": 0,
            "bite_log_entries": 12,
            "last_air_data": None,
            "last_nav_reference": None,
        }

    def receive(self, raw: bytes, *, now_ms: int) -> SerialAdmission:
        try:
            decoded = decode_native_frame(raw)
        except SerialLabError as exc:
            admission = SerialAdmission(False, str(exc), None, "no_state_change")
            self.received.append(admission)
            return admission
        if not decoded.crc_ok:
            admission = SerialAdmission(False, "crc", decoded, "no_state_change")
            self.received.append(admission)
            return admission
        if decoded.message_type not in self.accepted_types:
            admission = SerialAdmission(False, "unsupported_message", decoded, "no_state_change")
            self.received.append(admission)
            return admission
        policy = default_message_policies()[decoded.message_type]
        if policy.maintenance_only and not self.maintenance_port_enabled:
            admission = SerialAdmission(False, "maintenance_port_disabled", decoded, "no_state_change")
            self.received.append(admission)
            return admission
        effect = self._apply(decoded, now_ms)
        admission = SerialAdmission(True, "accepted", decoded, effect)
        self.received.append(admission)
        return admission

    def receive_event(
        self,
        event: SerialWireEvent,
        *,
        expected_baud_rate: int,
        now_ms: int,
        tolerance: float = 0.02,
    ) -> SerialAdmission:
        """Apply a digital baud-lock check before native frame parsing."""

        if expected_baud_rate <= 0 or abs(event.baud_rate / expected_baud_rate - 1.0) > tolerance:
            admission = SerialAdmission(False, "rate_mismatch", None, "no_state_change")
            self.received.append(admission)
            return admission
        return self.receive(event.observed, now_ms=now_ms)

    def _apply(self, decoded: DecodedSerialFrame, now_ms: int) -> str:
        payload = decoded.payload
        if decoded.message_type == "AIR_DATA_UPDATE":
            self.state["last_air_data"] = dict(payload)
            return f"air_data_updated:{payload.get('altitude_ft')}ft@{now_ms}ms"
        if decoded.message_type == "NAV_REFERENCE_UPDATE":
            self.state["last_nav_reference"] = dict(payload)
            return "nav_reference_updated"
        if decoded.message_type == "SET_MAINTENANCE_MODE":
            self.state["maintenance_mode"] = bool(payload.get("enabled"))
            return f"maintenance_mode:{self.state['maintenance_mode']}"
        if decoded.message_type == "LOAD_CONFIGURATION":
            self.state["calibration_id"] = str(payload.get("calibration_id", "UNKNOWN"))
            self.state["calibration_offset_ft"] = int(payload.get("offset_ft", 0))
            return f"configuration_loaded:{self.state['calibration_id']}"
        if decoded.message_type == "CLEAR_BITE_LOG":
            self.state["bite_log_entries"] = 0
            return "bite_log_cleared"
        if decoded.message_type == "BITE_LOG_READ":
            return f"bite_log_read:{self.state['bite_log_entries']}"
        if decoded.message_type == "BITE_LOG_RESPONSE":
            return "bite_log_response_received"
        return "no_state_change"


def default_message_policies() -> dict[str, SerialMessagePolicy]:
    """Synthetic ICD policy used only by this research laboratory."""

    return {
        "AIR_DATA_UPDATE": SerialMessagePolicy("AIR_DATA_UPDATE", roles=frozenset({"avionics_lru"})),
        "NAV_REFERENCE_UPDATE": SerialMessagePolicy("NAV_REFERENCE_UPDATE", roles=frozenset({"avionics_lru"})),
        "BITE_LOG_READ": SerialMessagePolicy("BITE_LOG_READ", maintenance_only=True, roles=frozenset({"approved_gse"})),
        "BITE_LOG_RESPONSE": SerialMessagePolicy("BITE_LOG_RESPONSE", maintenance_only=True, roles=frozenset({"air_data_lru"})),
        "SET_MAINTENANCE_MODE": SerialMessagePolicy("SET_MAINTENANCE_MODE", maintenance_only=True, roles=frozenset({"approved_gse"})),
        "LOAD_CONFIGURATION": SerialMessagePolicy("LOAD_CONFIGURATION", maintenance_only=True, roles=frozenset({"approved_gse"})),
        "CLEAR_BITE_LOG": SerialMessagePolicy("CLEAR_BITE_LOG", maintenance_only=True, roles=frozenset({"approved_gse"})),
    }


def _validate_payload(message_type: str, payload: Mapping[str, Any]) -> bool:
    """Apply conservative synthetic-ICD semantic checks at the gateway."""

    if message_type == "AIR_DATA_UPDATE":
        return (
            isinstance(payload.get("altitude_ft"), (int, float))
            and 0 <= float(payload["altitude_ft"]) <= 50_000
            and isinstance(payload.get("airspeed_kt"), (int, float))
            and 0 <= float(payload["airspeed_kt"]) <= 600
        )
    if message_type == "NAV_REFERENCE_UPDATE":
        return (
            isinstance(payload.get("latitude_deg"), (int, float))
            and -90 <= float(payload["latitude_deg"]) <= 90
            and isinstance(payload.get("longitude_deg"), (int, float))
            and -180 <= float(payload["longitude_deg"]) <= 180
        )
    if message_type == "SET_MAINTENANCE_MODE":
        return isinstance(payload.get("enabled"), bool)
    if message_type == "LOAD_CONFIGURATION":
        return (
            isinstance(payload.get("calibration_id"), str)
            and bool(payload["calibration_id"])
            and isinstance(payload.get("offset_ft"), int)
            and -1_000 <= payload["offset_ft"] <= 1_000
        )
    if message_type in {"BITE_LOG_READ", "CLEAR_BITE_LOG", "BITE_LOG_RESPONSE"}:
        return True
    return False


@dataclass(frozen=True)
class SecureSerialEnvelope:
    source: str
    role: str
    channel: str
    timestamp_ms: int
    sequence: int
    message_type: str
    native_raw: bytes
    tag: str


def _secure_input(envelope: SecureSerialEnvelope) -> bytes:
    return b"SERIAL-LAB-V1|" + b"|".join(
        (
            envelope.source.encode("utf-8"),
            envelope.role.encode("utf-8"),
            envelope.channel.encode("utf-8"),
            str(envelope.timestamp_ms).encode("ascii"),
            str(envelope.sequence).encode("ascii"),
            envelope.message_type.encode("ascii"),
            envelope.native_raw.hex().encode("ascii"),
        )
    )


def make_secure_envelope(
    *,
    source: str,
    role: str,
    channel: str,
    timestamp_ms: int,
    sequence: int,
    message_type: str,
    native_raw: bytes,
    secret: bytes,
) -> SecureSerialEnvelope:
    if not secret:
        raise SerialLabError("secret must not be empty")
    if timestamp_ms < 0 or sequence < 0:
        raise SerialLabError("timestamp_ms and sequence must be non-negative")
    unsigned = SecureSerialEnvelope(source, role, channel, timestamp_ms, sequence, message_type, native_raw, "")
    tag = hmac.new(secret, _secure_input(unsigned), hashlib.sha256).hexdigest()
    return replace(unsigned, tag=tag)


class SecureSerialGateway:
    """Security termination point for both serial deployment boundaries."""

    def __init__(
        self,
        *,
        secrets: Mapping[str, bytes],
        source_roles: Mapping[str, str],
        allowed_channels: Iterable[str],
        ground_state: str = "ON_GROUND",
        maintenance_session: bool = True,
        max_age_ms: int = 100,
        max_future_ms: int = 20,
        max_frames_per_window: int = 20,
        window_ms: int = 1_000,
    ) -> None:
        if not secrets or any(not key for key in secrets.values()):
            raise SerialLabError("secrets must contain non-empty keys")
        if max_age_ms < 0 or max_future_ms < 0 or max_frames_per_window <= 0 or window_ms <= 0:
            raise SerialLabError("invalid gateway timing or rate limits")
        self.secrets = dict(secrets)
        self.source_roles = dict(source_roles)
        self.allowed_channels = frozenset(allowed_channels)
        self.ground_state = ground_state
        self.maintenance_session = maintenance_session
        self.max_age_ms = max_age_ms
        self.max_future_ms = max_future_ms
        self.max_frames_per_window = max_frames_per_window
        self.window_ms = window_ms
        self._last_sequence: dict[tuple[str, str, str], int] = {}
        self._accepted_times: deque[int] = deque()
        self.accepted: list[SecureSerialEnvelope] = []

    def _rate_limited(self, now_ms: int) -> bool:
        cutoff = now_ms - self.window_ms
        while self._accepted_times and self._accepted_times[0] <= cutoff:
            self._accepted_times.popleft()
        return len(self._accepted_times) >= self.max_frames_per_window

    def admit(self, envelope: SecureSerialEnvelope, *, now_ms: int) -> SerialAdmission:
        try:
            decoded = decode_native_frame(envelope.native_raw)
        except SerialLabError as exc:
            return SerialAdmission(False, str(exc), None, "no_physical_output")
        if envelope.source not in self.secrets:
            return SerialAdmission(False, "unauthorized_source", decoded, "no_physical_output")
        if envelope.channel not in self.allowed_channels:
            return SerialAdmission(False, "unauthorized_channel", decoded, "no_physical_output")
        expected_role = self.source_roles.get(envelope.source)
        if expected_role is None or envelope.role != expected_role:
            return SerialAdmission(False, "unauthorized_role", decoded, "no_physical_output")
        if envelope.message_type != decoded.message_type:
            return SerialAdmission(False, "message_type_binding", decoded, "no_physical_output")
        expected_tag = hmac.new(self.secrets[envelope.source], _secure_input(replace(envelope, tag="")), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope.tag, expected_tag):
            return SerialAdmission(False, "authentication", decoded, "no_physical_output")
        age = now_ms - envelope.timestamp_ms
        if age > self.max_age_ms:
            return SerialAdmission(False, "stale", decoded, "no_physical_output")
        if age < -self.max_future_ms:
            return SerialAdmission(False, "future_timestamp", decoded, "no_physical_output")
        policy = default_message_policies().get(decoded.message_type)
        if policy is None or envelope.role not in policy.roles:
            return SerialAdmission(False, "role_not_permitted", decoded, "no_physical_output")
        if policy.maintenance_only and (self.ground_state != "ON_GROUND" or not self.maintenance_session):
            return SerialAdmission(False, "maintenance_not_available", decoded, "no_physical_output")
        if not _validate_payload(decoded.message_type, decoded.payload):
            return SerialAdmission(False, "semantic_validation", decoded, "no_physical_output")
        key = (envelope.source, envelope.channel, decoded.message_type)
        previous = self._last_sequence.get(key)
        if previous is not None and envelope.sequence <= previous:
            return SerialAdmission(False, "replay", decoded, "no_physical_output")
        if self._rate_limited(now_ms):
            return SerialAdmission(False, "rate_limit", decoded, "no_physical_output")
        self._last_sequence[key] = envelope.sequence
        self._accepted_times.append(now_ms)
        self.accepted.append(envelope)
        return SerialAdmission(True, "accepted", decoded, "native_output_authorized")


def _frame_summary(event: SerialWireEvent, admission: SerialAdmission | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "link": event.link_name,
        "direction": event.direction,
        "requested_ms": event.requested_ms,
        "start_ms": round(event.start_ms, 6),
        "end_ms": round(event.end_ms, 6),
        "wire_duration_ms": round(event.wire_duration_ms, 6),
        "baud_rate": event.baud_rate,
        "length_bytes": len(event.observed),
        "raw_hex": event.observed.hex(),
        "fault": event.fault,
    }
    if admission is not None:
        result.update(
            {
                "accepted": admission.accepted,
                "reason": admission.reason,
                "effect": admission.effect,
                "message_type": admission.decoded.message_type if admission.decoded else None,
                "payload": admission.decoded.payload if admission.decoded else None,
            }
        )
    return result


def _gateway() -> SecureSerialGateway:
    return SecureSerialGateway(
        secrets={
            "adc_lru": b"serial-adc-key",
            "approved_gse": b"serial-gse-key",
        },
        source_roles={"adc_lru": "avionics_lru", "approved_gse": "approved_gse"},
        allowed_channels={"RS422-AVIONICS-LRU", "RS232-GSE-MAINT"},
        ground_state="ON_GROUND",
        maintenance_session=True,
        max_age_ms=100,
        max_future_ms=20,
        max_frames_per_window=20,
    )


def _event_record(event: SerialWireEvent, admission: SerialAdmission | None = None) -> dict[str, Any]:
    return _frame_summary(event, admission)


def run_serial_scenarios() -> dict[str, Any]:
    """Run normal traffic, attack, and hardening scenarios for both links."""

    rs422 = SerialLink(RS422_AVIONICS_PROFILE)
    rs232 = SerialLink(RS232_MAINTENANCE_PROFILE)
    rs422_receiver = SerialReceiver(
        name="FMS_DISPLAY_LRU",
        accepted_types={"AIR_DATA_UPDATE", "NAV_REFERENCE_UPDATE"},
    )
    rs232_receiver = SerialReceiver(
        name="AIR_DATA_LRU_MAINT_PORT",
        accepted_types={
            "BITE_LOG_READ",
            "SET_MAINTENANCE_MODE",
            "LOAD_CONFIGURATION",
            "CLEAR_BITE_LOG",
        },
        maintenance_port_enabled=True,
    )
    gateway = _gateway()

    air_data_payload = {"altitude_ft": 30_000, "airspeed_kt": 440, "valid": True}
    air_data_raw = encode_native_frame("AIR_DATA_UPDATE", 1, air_data_payload)
    air_data_env = make_secure_envelope(
        source="adc_lru",
        role="avionics_lru",
        channel="RS422-AVIONICS-LRU",
        timestamp_ms=0,
        sequence=1,
        message_type="AIR_DATA_UPDATE",
        native_raw=air_data_raw,
        secret=b"serial-adc-key",
    )
    secure_air_admission = gateway.admit(air_data_env, now_ms=0)
    secure_air_event = rs422.transmit(
        air_data_raw,
        direction="ADC_LRU->FMS_DISPLAY_LRU",
        requested_ms=0,
    )
    secure_air_receiver = rs422_receiver.receive(secure_air_event.observed, now_ms=0)

    nav_raw = encode_native_frame(
        "NAV_REFERENCE_UPDATE",
        1,
        {"latitude_deg": 47.0, "longitude_deg": 122.0, "source_valid": True},
    )
    nav_event = rs422.transmit(
        nav_raw,
        direction="FMS_DISPLAY_LRU->ADC_LRU",
        requested_ms=0,
    )
    nav_receiver = SerialReceiver(name="ADC_LRU", accepted_types={"NAV_REFERENCE_UPDATE"})
    nav_admission = nav_receiver.receive(nav_event.observed, now_ms=0)
    simultaneous_full_duplex = {
        "same_requested_ms": True,
        "a_to_b_start_ms": secure_air_event.start_ms,
        "b_to_a_start_ms": nav_event.start_ms,
        "independent_direction_clocks": secure_air_event.start_ms == nav_event.start_ms,
    }

    # Direct injection into the exposed RS-422 TX direction. The native LRU
    # has no source field and therefore cannot distinguish this from ADC data.
    forged_raw = encode_native_frame(
        "AIR_DATA_UPDATE",
        900,
        {"altitude_ft": 500, "airspeed_kt": 80, "valid": True},
    )
    forged_event = rs422.transmit(
        forged_raw,
        direction="ADC_LRU->FMS_DISPLAY_LRU",
        requested_ms=100,
    )
    forged_native = rs422_receiver.receive(forged_event.observed, now_ms=100)
    forged_state_after = dict(rs422_receiver.state["last_air_data"] or {})
    forged_envelope = make_secure_envelope(
        source="attacker_adapter",
        role="avionics_lru",
        channel="RS422-AVIONICS-LRU",
        timestamp_ms=100,
        sequence=900,
        message_type="AIR_DATA_UPDATE",
        native_raw=forged_raw,
        secret=b"wrong-attacker-key",
    )
    forged_hardened = gateway.admit(forged_envelope, now_ms=100)

    # A captured native frame is modified and re-CRC'd. The native receiver
    # accepts it; the original authenticated envelope cannot be reused.
    tampered_raw = tamper_native_payload(
        air_data_raw,
        {"altitude_ft": 1_000, "airspeed_kt": 90, "valid": True},
    )
    tamper_event = rs422.transmit(
        tampered_raw,
        direction="ADC_LRU->FMS_DISPLAY_LRU",
        requested_ms=200,
    )
    tamper_native = rs422_receiver.receive(tamper_event.observed, now_ms=200)
    tampered_envelope = replace(air_data_env, native_raw=tampered_raw)
    tamper_hardened = gateway.admit(tampered_envelope, now_ms=200)

    replay_event = rs422.transmit(
        air_data_raw,
        direction="ADC_LRU->FMS_DISPLAY_LRU",
        requested_ms=300,
    )
    replay_native = rs422_receiver.receive(replay_event.observed, now_ms=300)
    replay_gateway = _gateway()
    replay_first_hardened = replay_gateway.admit(air_data_env, now_ms=0)
    replay_hardened = replay_gateway.admit(air_data_env, now_ms=20)

    capture = rs422.capture("ADC_LRU->FMS_DISPLAY_LRU")[0]
    captured_decoded = decode_native_frame(capture.observed)

    maintenance_raw = encode_native_frame(
        "LOAD_CONFIGURATION",
        20,
        {"calibration_id": "CAL-ATTACK", "offset_ft": 500},
    )
    maintenance_event = rs232.transmit(
        maintenance_raw,
        direction="GSE_LAPTOP->AIR_DATA_LRU",
        requested_ms=0,
    )
    maintenance_native = rs232_receiver.receive(maintenance_event.observed, now_ms=0)
    maintenance_env = make_secure_envelope(
        source="approved_gse",
        role="approved_gse",
        channel="RS232-GSE-MAINT",
        timestamp_ms=0,
        sequence=20,
        message_type="LOAD_CONFIGURATION",
        native_raw=maintenance_raw,
        secret=b"serial-gse-key",
    )
    maintenance_hardened = gateway.admit(maintenance_env, now_ms=0)

    clear_raw = encode_native_frame("CLEAR_BITE_LOG", 21, {})
    clear_event = rs232.transmit(
        clear_raw,
        direction="GSE_LAPTOP->AIR_DATA_LRU",
        requested_ms=50,
    )
    clear_native = rs232_receiver.receive(clear_event.observed, now_ms=50)
    clear_hardened = gateway.admit(
        make_secure_envelope(
            source="approved_gse",
            role="approved_gse",
            channel="RS232-GSE-MAINT",
            timestamp_ms=50,
            sequence=21,
            message_type="CLEAR_BITE_LOG",
            native_raw=clear_raw,
            secret=b"serial-gse-key",
        ),
        now_ms=50,
    )

    mismatch_link = SerialLink(replace(RS232_MAINTENANCE_PROFILE, baud_rate=9_600))
    mismatch_raw = encode_native_frame("BITE_LOG_READ", 99, {})
    mismatch_event = mismatch_link.transmit(
        mismatch_raw,
        direction="GSE_LAPTOP->AIR_DATA_LRU",
        requested_ms=0,
    )
    mismatch_receiver = SerialReceiver(name="MISMATCHED_LRU", accepted_types={"BITE_LOG_READ"})
    mismatch_admission = mismatch_receiver.receive_event(
        mismatch_event,
        expected_baud_rate=115_200,
        now_ms=0,
    )

    airborne_gateway = SecureSerialGateway(
        secrets={"approved_gse": b"serial-gse-key"},
        source_roles={"approved_gse": "approved_gse"},
        allowed_channels={"RS232-GSE-MAINT"},
        ground_state="AIRBORNE",
        maintenance_session=False,
    )
    airborne_maintenance = airborne_gateway.admit(maintenance_env, now_ms=0)

    flood_link = SerialLink(RS232_MAINTENANCE_PROFILE)
    flood_raw = encode_native_frame("BITE_LOG_READ", 100, {})
    flood_native_results: list[SerialAdmission] = []
    flood_events: list[SerialWireEvent] = []
    for index in range(64):
        event = flood_link.transmit(
            flood_raw,
            direction="GSE_LAPTOP->AIR_DATA_LRU",
            requested_ms=0,
        )
        flood_events.append(event)
        flood_native_results.append(rs232_receiver.receive(event.observed, now_ms=0))
    legal_maintenance_raw = encode_native_frame("BITE_LOG_READ", 200, {})
    legal_event = flood_link.transmit(
        legal_maintenance_raw,
        direction="GSE_LAPTOP->AIR_DATA_LRU",
        requested_ms=0,
    )
    legal_native = rs232_receiver.receive(legal_event.observed, now_ms=0)
    flood_gateway = _gateway()
    flood_hardened = [
        flood_gateway.admit(
            make_secure_envelope(
                source="attacker_adapter",
                role="approved_gse",
                channel="RS232-GSE-MAINT",
                timestamp_ms=0,
                sequence=index,
                message_type="BITE_LOG_READ",
                native_raw=flood_raw,
                secret=b"wrong-attacker-key",
            ),
            now_ms=0,
        )
        for index in range(64)
    ]
    legal_hardened = flood_gateway.admit(
        make_secure_envelope(
            source="approved_gse",
            role="approved_gse",
            channel="RS232-GSE-MAINT",
            timestamp_ms=0,
            sequence=200,
            message_type="BITE_LOG_READ",
            native_raw=legal_maintenance_raw,
            secret=b"serial-gse-key",
        ),
        now_ms=0,
    )

    return {
        "model": {
            "synthetic_frame": "SYNC|VERSION|TYPE|SEQUENCE|LENGTH|JSON_PAYLOAD|CRC16",
            "crc": "CRC-16/CCITT-FALSE",
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "command_codes": {name: f"0x{code:02x}" for name, code in COMMAND_CODES.items()},
        },
        "interfaces": {
            "rs232": RS232_MAINTENANCE_PROFILE.profile,
            "rs422": RS422_AVIONICS_PROFILE.profile,
        },
        "normal": {
            "rs422": {
                "gateway": {"accepted": secure_air_admission.accepted, "reason": secure_air_admission.reason},
                "receiver": {"accepted": secure_air_receiver.accepted, "reason": secure_air_receiver.reason},
                "effect": secure_air_receiver.effect,
                "wire": _event_record(secure_air_event),
            },
            "rs232": {
                "full_duplex": simultaneous_full_duplex,
                "maintenance_direct_receiver": {
                    "accepted": maintenance_native.accepted,
                    "reason": maintenance_native.reason,
                    "effect": maintenance_native.effect,
                },
                "maintenance_gateway": {"accepted": maintenance_hardened.accepted, "reason": maintenance_hardened.reason},
                "clear_log_direct_receiver": {
                    "accepted": clear_native.accepted,
                    "reason": clear_native.reason,
                    "effect": clear_native.effect,
                },
                "clear_log_gateway": {"accepted": clear_hardened.accepted, "reason": clear_hardened.reason},
                "configuration_mismatch": {
                    "sender_baud_rate": mismatch_event.baud_rate,
                    "receiver_expected_baud_rate": 115_200,
                    "accepted": mismatch_admission.accepted,
                    "reason": mismatch_admission.reason,
                    "wire": _event_record(mismatch_event),
                },
            },
        },
        "attacks": {
            "physical_tap_and_capture": {
                "link": capture.link_name,
                "direction": capture.direction,
                "captured_length_bytes": len(capture.observed),
                "captured_raw_hex": capture.observed.hex(),
                "decoded_message_type": captured_decoded.message_type,
                "decoded_payload": captured_decoded.payload,
                "confidentiality": False,
            },
            "injection": {
                "native": {
                    "accepted": forged_native.accepted,
                    "reason": forged_native.reason,
                    "effect": forged_native.effect,
                    "state_after": forged_state_after,
                },
                "hardened": {"accepted": forged_hardened.accepted, "reason": forged_hardened.reason, "effect": forged_hardened.effect},
                "wire": _event_record(forged_event),
            },
            "tamper_recomputed_crc": {
                "native": {"accepted": tamper_native.accepted, "reason": tamper_native.reason, "effect": tamper_native.effect},
                "hardened": {"accepted": tamper_hardened.accepted, "reason": tamper_hardened.reason, "effect": tamper_hardened.effect},
                "crc_valid_after_tamper": tamper_native.decoded.crc_ok if tamper_native.decoded else False,
                "wire": _event_record(tamper_event),
            },
            "replay": {
                "native": {"accepted": replay_native.accepted, "reason": replay_native.reason, "effect": replay_native.effect},
                "first_hardened": {"accepted": replay_first_hardened.accepted, "reason": replay_first_hardened.reason},
                "hardened": {"accepted": replay_hardened.accepted, "reason": replay_hardened.reason, "effect": replay_hardened.effect},
                "wire": _event_record(replay_event),
            },
            "airborne_maintenance": {
                "direct_native": {"accepted": maintenance_native.accepted, "reason": maintenance_native.reason},
                "hardened_gateway": {"accepted": airborne_maintenance.accepted, "reason": airborne_maintenance.reason},
            },
            "flood": {
                "attacker_frames": len(flood_events),
                "native_attacker_accepted": sum(item.accepted for item in flood_native_results),
                "native_legal_start_ms": legal_event.start_ms,
                "native_legal_effect": legal_native.effect,
                "hardened_attacker_rejected": sum(not item.accepted for item in flood_hardened),
                "hardened_legal_admitted": legal_hardened.accepted,
                "hardened_legal_start_ms": 0.0,
                "deadline_ms": 20.0,
                "native_deadline_missed": legal_event.start_ms > 20.0,
                "hardened_deadline_missed": False,
            },
        },
        "state": {
            "rs422_receiver": rs422_receiver.state,
            "rs232_receiver": rs232_receiver.state,
        },
        "limitations": [
            "RS-232/RS-422 profiles model digital framing and timing only; no voltage, common-mode, impedance, termination, EMI, ESD, cable fault, or transceiver behavior is measured.",
            "The frame format, command codes, LRU names, payload ranges, and maintenance effects are synthetic project ICD data, not a real aircraft ICD.",
            "CRC-16 is modeled as transmission-error detection; it is not source authentication, authorization, confidentiality, or anti-replay protection.",
            "The lab does not open /dev/tty devices and does not connect to an aircraft, certified LRU, or maintenance GSE.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_serial_scenarios(), ensure_ascii=False, indent=2, sort_keys=True))
