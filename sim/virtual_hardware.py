"""Deterministic virtual hardware models for the civil-aviation lab.

The models expose the interfaces and failure modes needed to exercise the
software integration before hardware is available:

* :class:`VirtualArinc429Card` models digital TX/RX channels, nominal line
  rates, FIFO admission, word timing, loopback capture, and bit faults.
* :class:`VirtualLru` models a configured ARINC 429 receiver using a synthetic
  ICD profile.
* :class:`VirtualPx4Board` models the PX4 HIL endpoint, including sensor/GPS
  freshness, actuator feedback, and a disarmed fail-safe default.

No serial device, socket, GPIO, electrical transceiver, or aircraft system is
opened. These are software models, not replacements for voltage, impedance,
EMI, MCU interrupt, or real LRU tests.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

from .arinc429_lab import (
    BusFrame,
    DecodedWord,
    LegacyReceiver,
    decode_word,
    encode_word,
    timing_lock,
)


class VirtualHardwareError(ValueError):
    """Raised when a virtual hardware configuration or operation is invalid."""


@dataclass(frozen=True)
class VirtualArinc429CardConfig:
    """Explicit digital profile for the virtual ARINC 429 interface card."""

    model: str = "V429-LAB-2T4R"
    tx_channels: int = 2
    rx_channels: int = 4
    bit_rate_bps: int = 100_000
    word_bits: int = 32
    interword_gap_bits: int = 4
    tx_fifo_depth: int = 64
    loopback: bool = True

    def validate(self) -> None:
        if not self.model:
            raise VirtualHardwareError("card model must not be empty")
        if self.tx_channels <= 0 or self.rx_channels <= 0:
            raise VirtualHardwareError("card must expose at least one TX and RX channel")
        if self.bit_rate_bps not in (12_500, 100_000):
            raise VirtualHardwareError("bit_rate_bps must be 12500 or 100000")
        if self.word_bits != 32:
            raise VirtualHardwareError("ARINC 429 word_bits must be 32")
        if self.interword_gap_bits < 0:
            raise VirtualHardwareError("interword_gap_bits must be non-negative")
        if self.tx_fifo_depth <= 0:
            raise VirtualHardwareError("tx_fifo_depth must be positive")


@dataclass(frozen=True)
class Arinc429WireEvent:
    """One digital word scheduled on a virtual ARINC 429 channel."""

    tx_channel: int
    rx_channel: int | None
    requested_ms: int
    start_ms: float
    end_ms: float
    bit_rate_bps: int
    raw_word: int
    observed_raw_word: int
    fault: str | None = None

    @property
    def word_duration_ms(self) -> float:
        return self.end_ms - self.start_ms


class VirtualArinc429Card:
    """Digital loopback model of a named ARINC 429 interface card.

    The card accepts raw 32-bit words. Security metadata is deliberately not
    carried on the simulated wire; authentication is expected to terminate in
    a gateway before a native word is emitted. This keeps the model aligned
    with the ARINC 429 word boundary while still allowing the lab gateway to
    be tested before the card.
    """

    def __init__(self, config: VirtualArinc429CardConfig | None = None) -> None:
        self.config = config or VirtualArinc429CardConfig()
        self.config.validate()
        self._next_tx_ms = [0.0] * self.config.tx_channels
        self._pending: deque[Arinc429WireEvent] = deque()
        self.events: list[Arinc429WireEvent] = []
        self.dropped_words = 0
        self.overflow_words = 0

    @property
    def profile(self) -> dict[str, object]:
        return {
            "model": self.config.model,
            "tx_channels": self.config.tx_channels,
            "rx_channels": self.config.rx_channels,
            "bit_rate_bps": self.config.bit_rate_bps,
            "word_bits": self.config.word_bits,
            "interword_gap_bits": self.config.interword_gap_bits,
            "tx_fifo_depth": self.config.tx_fifo_depth,
            "loopback": self.config.loopback,
            "electrical_model": False,
        }

    def _validate_channel(self, tx_channel: int, rx_channel: int | None) -> None:
        if not 0 <= tx_channel < self.config.tx_channels:
            raise VirtualHardwareError(f"invalid TX channel {tx_channel}")
        if rx_channel is not None and not 0 <= rx_channel < self.config.rx_channels:
            raise VirtualHardwareError(f"invalid RX channel {rx_channel}")

    def submit_tx(
        self,
        *,
        tx_channel: int,
        raw_word: int,
        requested_ms: int,
        rx_channel: int | None = 0,
        bit_flip_mask: int = 0,
        drop: bool = False,
    ) -> Arinc429WireEvent | None:
        """Schedule one word and return its digital wire event.

        ``bit_flip_mask`` and ``drop`` are authorized-lab fault controls. They
        are applied after FIFO admission and before the virtual receiver. A
        dropped word is recorded as a fault but does not reach the receiver.
        """

        if not isinstance(raw_word, int) or not 0 <= raw_word <= 0xFFFFFFFF:
            raise VirtualHardwareError("raw_word must be an unsigned 32-bit integer")
        if requested_ms < 0:
            raise VirtualHardwareError("requested_ms must be non-negative")
        if not 0 <= bit_flip_mask <= 0xFFFFFFFF:
            raise VirtualHardwareError("bit_flip_mask must be an unsigned 32-bit integer")
        self._validate_channel(tx_channel, rx_channel)
        if len(self._pending) >= self.config.tx_fifo_depth:
            self.overflow_words += 1
            raise VirtualHardwareError("tx_fifo_overflow")

        bit_time_ms = 1_000.0 / self.config.bit_rate_bps
        word_time_ms = (self.config.word_bits + self.config.interword_gap_bits) * bit_time_ms
        start_ms = max(float(requested_ms), self._next_tx_ms[tx_channel])
        end_ms = start_ms + word_time_ms
        self._next_tx_ms[tx_channel] = end_ms
        observed = raw_word ^ bit_flip_mask
        fault = None
        if bit_flip_mask:
            fault = f"bit_flip_mask:0x{bit_flip_mask:08x}"
        if drop:
            fault = "drop" if fault is None else f"{fault},drop"
            self.dropped_words += 1
        event = Arinc429WireEvent(
            tx_channel=tx_channel,
            rx_channel=rx_channel if self.config.loopback and not drop else None,
            requested_ms=requested_ms,
            start_ms=start_ms,
            end_ms=end_ms,
            bit_rate_bps=self.config.bit_rate_bps,
            raw_word=raw_word,
            observed_raw_word=observed,
            fault=fault,
        )
        self.events.append(event)
        if not drop and self.config.loopback:
            self._pending.append(event)
        return event

    def drain_rx(self) -> list[Arinc429WireEvent]:
        """Return all loopback observations currently available."""

        observations = list(self._pending)
        self._pending.clear()
        return observations

    def reset(self) -> None:
        """Reset queues and deterministic channel clocks."""

        self._next_tx_ms = [0.0] * self.config.tx_channels
        self._pending.clear()
        self.events.clear()
        self.dropped_words = 0
        self.overflow_words = 0


@dataclass(frozen=True)
class LruInput:
    """Decoded observation delivered to a virtual LRU consumer."""

    timestamp_ms: int
    channel: int
    raw_word: int
    decoded: DecodedWord
    start_ms: float
    end_ms: float
    accepted: bool
    reason: str


class VirtualLru:
    """Synthetic-ICD ARINC receiver representing one avionics LRU."""

    def __init__(
        self,
        *,
        name: str,
        accepted_labels: Iterable[int],
        expected_bit_rate_bps: int = 100_000,
    ) -> None:
        if not name:
            raise VirtualHardwareError("LRU name must not be empty")
        if expected_bit_rate_bps not in (12_500, 100_000):
            raise VirtualHardwareError("expected_bit_rate_bps must be 12500 or 100000")
        self.name = name
        self.expected_bit_rate_bps = expected_bit_rate_bps
        self.receiver = LegacyReceiver(accepted_labels)
        self.inputs: list[LruInput] = []

    def consume(self, event: Arinc429WireEvent, *, observed_at_ms: int) -> LruInput:
        decoded = decode_word(event.observed_raw_word)
        if not timing_lock(self.expected_bit_rate_bps, self._bit_rate_for(event), tolerance=0.02):
            accepted = False
            reason = "rate_mismatch"
        else:
            admission = self.receiver.receive(
                BusFrame(observed_at_ms, f"{self.name}.arinc429", event.observed_raw_word)
            )
            accepted = admission.accepted
            reason = admission.reason
        result = LruInput(
            timestamp_ms=observed_at_ms,
            channel=event.rx_channel if event.rx_channel is not None else -1,
            raw_word=event.observed_raw_word,
            decoded=decoded,
            start_ms=event.start_ms,
            end_ms=event.end_ms,
            accepted=accepted,
            reason=reason,
        )
        self.inputs.append(result)
        return result

    @staticmethod
    def _bit_rate_for(event: Arinc429WireEvent) -> int:
        return event.bit_rate_bps

    @property
    def accepted_count(self) -> int:
        return sum(item.accepted for item in self.inputs)

    @property
    def rejected_count(self) -> int:
        return sum(not item.accepted for item in self.inputs)


@dataclass(frozen=True)
class VirtualActuatorFeedback:
    timestamp_ms: int
    controls_first_8: tuple[float, ...]
    armed: bool
    failsafe: bool
    reason: str


class VirtualPx4Board:
    """Host-side model of a PX4 FMU HIL endpoint.

    It deliberately defaults to disarmed and never produces non-zero
    actuator commands. The model is useful for transport, timing, watchdog,
    and evidence tests; it is not an emulation of a particular MCU board.
    """

    def __init__(
        self,
        *,
        board_model: str = "PX4-FMU-VIRTUAL",
        firmware: str = "PX4-HIL-virtual",
        sensor_timeout_ms: int = 100,
        gps_timeout_ms: int = 500,
    ) -> None:
        if not board_model or not firmware:
            raise VirtualHardwareError("board model and firmware must not be empty")
        if sensor_timeout_ms <= 0 or gps_timeout_ms <= 0:
            raise VirtualHardwareError("watchdog timeouts must be positive")
        self.board_model = board_model
        self.firmware = firmware
        self.sensor_timeout_ms = sensor_timeout_ms
        self.gps_timeout_ms = gps_timeout_ms
        self.connected = False
        self.hitl_enabled = False
        self.armed = False
        self.sensor_count = 0
        self.gps_count = 0
        self.feedback: list[VirtualActuatorFeedback] = []
        self.status: list[str] = []
        self._last_sensor_ms: int | None = None
        self._last_gps_ms: int | None = None

    @property
    def profile(self) -> dict[str, object]:
        return {
            "board_model": self.board_model,
            "firmware": self.firmware,
            "hitl_enabled": self.hitl_enabled,
            "sensor_timeout_ms": self.sensor_timeout_ms,
            "gps_timeout_ms": self.gps_timeout_ms,
            "physical_mcu_model": False,
        }

    def connect(self) -> None:
        self.connected = True
        self.hitl_enabled = True
        self.status.append("HIL_CONNECTED")

    def request_arm(self) -> bool:
        """Keep the virtual bench disarmed unless an explicit model is added."""

        self.status.append("ARM_REJECTED_VIRTUAL_BENCH")
        self.armed = False
        return False

    def receive_hil_sensor(self, timestamp_ms: int, values: Mapping[str, float]) -> None:
        if timestamp_ms < 0 or not self.connected or not self.hitl_enabled:
            raise VirtualHardwareError("board is not ready for HIL_SENSOR")
        if not values:
            raise VirtualHardwareError("HIL_SENSOR values must not be empty")
        self.sensor_count += 1
        self._last_sensor_ms = timestamp_ms

    def receive_hil_gps(self, timestamp_ms: int, values: Mapping[str, float | int]) -> None:
        if timestamp_ms < 0 or not self.connected or not self.hitl_enabled:
            raise VirtualHardwareError("board is not ready for HIL_GPS")
        if not values:
            raise VirtualHardwareError("HIL_GPS values must not be empty")
        self.gps_count += 1
        self._last_gps_ms = timestamp_ms

    def step(self, timestamp_ms: int) -> VirtualActuatorFeedback:
        if timestamp_ms < 0:
            raise VirtualHardwareError("timestamp_ms must be non-negative")
        if not self.connected or not self.hitl_enabled:
            failsafe = True
            reason = "not_connected"
        elif self._last_sensor_ms is None or timestamp_ms - self._last_sensor_ms > self.sensor_timeout_ms:
            failsafe = True
            reason = "sensor_timeout"
        elif self._last_gps_ms is None or timestamp_ms - self._last_gps_ms > self.gps_timeout_ms:
            failsafe = True
            reason = "gps_timeout"
        else:
            failsafe = False
            reason = "disarmed_zero_output"
        output = VirtualActuatorFeedback(
            timestamp_ms=timestamp_ms,
            controls_first_8=(0.0,) * 8,
            armed=self.armed,
            failsafe=failsafe,
            reason=reason,
        )
        self.feedback.append(output)
        return output


def encode_state_as_hil_values(state: Mapping[str, float | int]) -> dict[str, float]:
    """Map a state summary to a small deterministic HIL sensor payload."""

    required = ("altitude_ft", "true_airspeed_kt", "heading_deg", "pitch_deg", "roll_deg")
    missing = [name for name in required if name not in state]
    if missing:
        raise VirtualHardwareError(f"HIL state is missing fields: {', '.join(missing)}")
    return {name: float(state[name]) for name in required}


def synthetic_lru_labels() -> tuple[int, ...]:
    """Return the labels defined by the current project's synthetic ICD."""

    return (0o203, 0o210, 0o211, 0o212)


def virtual_card_probe_word(label: int = 0o203, data: int = 1000) -> int:
    """Create a valid probe word for a virtual card loopback test."""

    return encode_word(label, 0, data, 0)
