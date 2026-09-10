"""Run the local virtual hardware-in-the-loop aviation lab.

The runner composes the existing civil flight model and security gateway with
the digital models in :mod:`sim.virtual_hardware`. It is deliberately
deterministic and has no serial, socket, GPIO, USB, PCIe, or aircraft I/O.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .arinc429_lab import BusFrame, SecureGateway, encode_word, tamper_data
from .civil_aviation_platform import (
    Arinc429SensorPublisher,
    CivilScenario,
    JsbsimCivilModel,
    KinematicCivilModel,
    build_gateway,
)
from .virtual_hardware import (
    VirtualArinc429Card,
    VirtualArinc429CardConfig,
    VirtualHardwareError,
    VirtualLru,
    VirtualPx4Board,
    encode_state_as_hil_values,
    synthetic_lru_labels,
)


def _state_summary(state: Any) -> dict[str, object]:
    return {
        "timestamp_ms": state.timestamp_ms,
        "phase": state.phase,
        "latitude_deg": round(state.latitude_deg, 7),
        "longitude_deg": round(state.longitude_deg, 7),
        "altitude_ft": round(state.altitude_ft, 2),
        "true_airspeed_kt": round(state.true_airspeed_kt, 2),
        "heading_deg": round(state.heading_deg, 2),
        "vertical_speed_fpm": round(state.vertical_speed_fpm, 2),
        "pitch_deg": round(state.pitch_deg, 2),
        "roll_deg": round(state.roll_deg, 2),
    }


def _wire_summary(event: Any) -> dict[str, object]:
    return {
        "tx_channel": event.tx_channel,
        "rx_channel": event.rx_channel,
        "requested_ms": event.requested_ms,
        "start_ms": round(event.start_ms, 6),
        "end_ms": round(event.end_ms, 6),
        "word_duration_ms": round(event.word_duration_ms, 6),
        "bit_rate_bps": event.bit_rate_bps,
        "raw_word": f"0x{event.raw_word:08x}",
        "observed_raw_word": f"0x{event.observed_raw_word:08x}",
        "fault": event.fault,
    }


def _admission_summary(admission: Any) -> dict[str, object]:
    return {"accepted": admission.accepted, "reason": admission.reason}


def _make_model(engine: str, aircraft: str, jsbsim_root: str | Path) -> tuple[Any, dict[str, object]]:
    if engine not in {"auto", "kinematic", "jsbsim"}:
        raise VirtualHardwareError(f"unsupported engine: {engine}")
    if engine == "kinematic":
        return KinematicCivilModel(CivilScenario()), {
            "requested": engine,
            "selected": "kinematic",
            "aircraft": aircraft,
        }
    try:
        return JsbsimCivilModel(aircraft=aircraft, root_dir=jsbsim_root), {
            "requested": engine,
            "selected": "jsbsim",
            "aircraft": aircraft,
            "root_dir": str(Path(jsbsim_root).resolve()),
        }
    except (ImportError, OSError, ValueError) as exc:
        if engine == "jsbsim":
            raise
        return KinematicCivilModel(CivilScenario()), {
            "requested": engine,
            "selected": "kinematic",
            "aircraft": aircraft,
            "fallback_reason": str(exc),
        }


def _accepted_normal_inputs(card: VirtualArinc429Card, lru: VirtualLru, timestamp_ms: int) -> list[Any]:
    inputs: list[Any] = []
    for event in card.drain_rx():
        inputs.append(lru.consume(event, observed_at_ms=timestamp_ms))
    return inputs


def _attack_event(
    attack: str,
    timestamp_ms: int,
    current_frames: list[BusFrame],
    previous_frames: list[BusFrame] | None,
    gateway: SecureGateway,
    card: VirtualArinc429Card,
    lru: VirtualLru,
) -> dict[str, object] | None:
    if attack == "none":
        return None
    if attack == "rate_mismatch":
        return {
            "type": attack,
            "status": "card_and_lru_rate_profiles_are_intentionally_different",
        }

    target = current_frames[0]
    if attack == "injection":
        decoded = target.decoded
        forged_raw = encode_word(decoded.label, decoded.sdi, decoded.data + 1_000, decoded.ssm)
        forged = BusFrame(timestamp_ms, "attacker", forged_raw)
        hardened = gateway.admit(forged, timestamp_ms)
        wire = card.submit_tx(
            tx_channel=0,
            rx_channel=0,
            raw_word=forged_raw,
            requested_ms=timestamp_ms,
        )
        card.drain_rx()
        legacy = lru.consume(wire, observed_at_ms=timestamp_ms) if wire is not None else None
        return {
            "type": attack,
            "legacy_lru": {
                "accepted": bool(legacy and legacy.accepted),
                "reason": legacy.reason if legacy else "not_transmitted",
            },
            "hardened_gateway": _admission_summary(hardened),
            "hardened_physical_output": False,
            "wire": _wire_summary(wire) if wire is not None else None,
        }

    if previous_frames is None:
        return {"type": attack, "status": "not_triggered_no_previous_frame"}

    previous = previous_frames[0]
    if attack == "replay":
        replay_wire = card.submit_tx(
            tx_channel=0,
            rx_channel=0,
            raw_word=previous.raw_word,
            requested_ms=timestamp_ms,
        )
        card.drain_rx()
        replay_input = lru.consume(replay_wire, observed_at_ms=timestamp_ms) if replay_wire else None
        hardened = gateway.admit(previous, timestamp_ms)
        return {
            "type": attack,
            "legacy_lru": {
                "accepted": bool(replay_input and replay_input.accepted),
                "reason": replay_input.reason if replay_input else "not_transmitted",
            },
            "hardened_gateway": _admission_summary(hardened),
            "hardened_physical_output": False,
            "wire": _wire_summary(replay_wire) if replay_wire is not None else None,
        }

    if attack == "tamper":
        tampered_raw = tamper_data(previous.raw_word, previous.decoded.data + 1_000)
        tampered = replace(previous, raw_word=tampered_raw)
        tamper_wire = card.submit_tx(
            tx_channel=0,
            rx_channel=0,
            raw_word=tampered_raw,
            requested_ms=timestamp_ms,
        )
        card.drain_rx()
        tamper_input = lru.consume(tamper_wire, observed_at_ms=timestamp_ms) if tamper_wire else None
        hardened = gateway.admit(tampered, timestamp_ms)
        return {
            "type": attack,
            "legacy_lru": {
                "accepted": bool(tamper_input and tamper_input.accepted),
                "reason": tamper_input.reason if tamper_input else "not_transmitted",
            },
            "parity_valid": tampered.decoded.parity_ok,
            "hardened_gateway": _admission_summary(hardened),
            "hardened_physical_output": False,
            "wire": _wire_summary(tamper_wire) if tamper_wire is not None else None,
        }

    if attack == "flood":
        attacker_frames = [
            BusFrame(
                timestamp_ms,
                "attacker",
                encode_word(target.decoded.label, target.decoded.sdi, 2_000 + index, target.decoded.ssm),
            )
            for index in range(64)
        ]
        hardened_results = [gateway.admit(frame, timestamp_ms) for frame in attacker_frames]
        transmitted: list[Any] = []
        overflow = 0
        for frame in attacker_frames:
            try:
                event = card.submit_tx(
                    tx_channel=0,
                    rx_channel=0,
                    raw_word=frame.raw_word,
                    requested_ms=timestamp_ms,
                )
            except VirtualHardwareError as exc:
                if str(exc) != "tx_fifo_overflow":
                    raise
                overflow += 1
            else:
                if event is not None:
                    transmitted.append(event)
        legacy_results = [lru.consume(event, observed_at_ms=timestamp_ms) for event in card.drain_rx()]
        return {
            "type": attack,
            "attacker_frames": len(attacker_frames),
            "legacy_lru_accepted": sum(item.accepted for item in legacy_results),
            "hardened_gateway_rejected": sum(not item.accepted for item in hardened_results),
            "physical_events_submitted": len(transmitted),
            "tx_fifo_overflow": overflow,
            "hardened_physical_output": False,
            "first_wire": _wire_summary(transmitted[0]) if transmitted else None,
            "last_wire": _wire_summary(transmitted[-1]) if transmitted else None,
        }

    raise VirtualHardwareError(f"unsupported attack: {attack}")


def run_virtual_hardware_lab(
    *,
    duration_ms: int = 2_000,
    step_ms: int = 20,
    engine: str = "kinematic",
    aircraft: str = "737",
    jsbsim_root: str | Path = "/tmp/civil_aviation_jsbsim_deps/jsbsim",
    attack: str = "none",
    attack_at_ms: int = 1_000,
    card_bit_rate_bps: int = 100_000,
    lru_bit_rate_bps: int | None = None,
) -> dict[str, object]:
    """Run a fully local virtual-card, virtual-LRU, and virtual-PX4 loop."""

    if duration_ms <= 0 or step_ms <= 0:
        raise VirtualHardwareError("duration_ms and step_ms must be positive")
    if attack_at_ms < 0 or (attack != "none" and attack_at_ms > duration_ms):
        raise VirtualHardwareError("attack_at_ms must be inside the simulation interval")
    if attack not in {"none", "injection", "replay", "tamper", "flood", "rate_mismatch"}:
        raise VirtualHardwareError(f"unsupported attack: {attack}")
    if card_bit_rate_bps not in (12_500, 100_000):
        raise VirtualHardwareError("card_bit_rate_bps must be 12500 or 100000")
    expected_lru_rate = lru_bit_rate_bps or card_bit_rate_bps
    if attack == "rate_mismatch" and lru_bit_rate_bps is None:
        expected_lru_rate = 12_500 if card_bit_rate_bps == 100_000 else 100_000
    if expected_lru_rate not in (12_500, 100_000):
        raise VirtualHardwareError("lru_bit_rate_bps must be 12500 or 100000")
    if attack != "none" and attack_at_ms % step_ms != 0:
        raise VirtualHardwareError("attack_at_ms must align with step_ms")

    model, engine_info = _make_model(engine, aircraft, jsbsim_root)
    card = VirtualArinc429Card(
        VirtualArinc429CardConfig(
            bit_rate_bps=card_bit_rate_bps,
            loopback=True,
        )
    )
    lru = VirtualLru(
        name="SYNTHETIC_AIR_DATA_LRU",
        accepted_labels=synthetic_lru_labels(),
        expected_bit_rate_bps=expected_lru_rate,
    )
    board = VirtualPx4Board()
    board.connect()
    arm_request_result = board.request_arm()
    gateway = build_gateway(step_ms)
    publisher = Arinc429SensorPublisher()
    timestamps = list(range(0, duration_ms + 1, step_ms))
    previous_frames: list[BusFrame] | None = None
    trace: list[dict[str, object]] = []
    attack_event: dict[str, object] | None = None
    normal_frame_count = 0
    secure_admitted_count = 0
    normal_lru_accepted = 0
    normal_lru_rejected = 0
    normal_wire_events = 0
    rejection_reasons: dict[str, int] = {}

    for timestamp_ms in timestamps:
        state = model.step(timestamp_ms)
        frames = publisher.publish(state)
        normal_frame_count += len(frames)
        for frame in frames:
            admission = gateway.admit(frame, timestamp_ms)
            if admission.accepted:
                secure_admitted_count += 1
                card.submit_tx(
                    tx_channel=0,
                    rx_channel=0,
                    raw_word=frame.raw_word,
                    requested_ms=timestamp_ms,
                )
            else:
                rejection_reasons[admission.reason] = rejection_reasons.get(admission.reason, 0) + 1

        normal_inputs = _accepted_normal_inputs(card, lru, timestamp_ms)
        normal_wire_events += len(normal_inputs)
        normal_lru_accepted += sum(item.accepted for item in normal_inputs)
        normal_lru_rejected += sum(not item.accepted for item in normal_inputs)
        if normal_lru_accepted > 0 or normal_inputs:
            if any(item.accepted for item in normal_inputs):
                board.receive_hil_sensor(timestamp_ms, encode_state_as_hil_values(_state_summary(state)))
                if timestamp_ms % 100 == 0:
                    board.receive_hil_gps(
                        timestamp_ms,
                        {
                            "lat_deg": state.latitude_deg,
                            "lon_deg": state.longitude_deg,
                            "altitude_ft": state.altitude_ft,
                            "fix_type": 3,
                        },
                    )

        if timestamp_ms == attack_at_ms:
            attack_event = _attack_event(
                attack,
                timestamp_ms,
                frames,
                previous_frames,
                gateway,
                card,
                lru,
            )

        feedback = board.step(timestamp_ms)
        trace.append(
            {
                "timestamp_ms": timestamp_ms,
                "state": _state_summary(state),
                "normal_wire_events": len(normal_inputs),
                "normal_lru_accepted": sum(item.accepted for item in normal_inputs),
                "normal_lru_rejected": sum(not item.accepted for item in normal_inputs),
                "board_sensor_count": board.sensor_count,
                "board_gps_count": board.gps_count,
                "actuator_feedback": {
                    "controls_first_8": list(feedback.controls_first_8),
                    "armed": feedback.armed,
                    "failsafe": feedback.failsafe,
                    "reason": feedback.reason,
                },
            }
        )
        previous_frames = frames

    failsafe_count = sum(item.failsafe for item in board.feedback)
    return {
        "platform": {
            "name": "civil-aviation-virtual-hardware-lab",
            "version": "0.1",
            "scope": "digital hardware model; no physical I/O",
            "duration_ms": duration_ms,
            "step_ms": step_ms,
            "engine": engine_info,
            "synthetic_icd": True,
        },
        "card": {
            "profile": card.profile,
            "events_total": len(card.events),
            "normal_wire_events": normal_wire_events,
            "dropped_words": card.dropped_words,
            "overflow_words": card.overflow_words,
            "event_samples": [_wire_summary(event) for event in card.events[:8]],
        },
        "lru": {
            "name": lru.name,
            "accepted_labels": [oct(label) for label in synthetic_lru_labels()],
            "expected_bit_rate_bps": expected_lru_rate,
            "accepted_count": lru.accepted_count,
            "rejected_count": lru.rejected_count,
            "rejection_reasons": {
                reason: sum(item.reason == reason for item in lru.inputs)
                for reason in sorted({item.reason for item in lru.inputs if not item.accepted})
            },
        },
        "board": {
            "profile": board.profile,
            "connected": board.connected,
            "hitl_enabled": board.hitl_enabled,
            "arm_request_accepted": arm_request_result,
            "armed": board.armed,
            "sensor_count": board.sensor_count,
            "gps_count": board.gps_count,
            "actuator_feedback_count": len(board.feedback),
            "failsafe_count": failsafe_count,
            "status": board.status,
        },
        "normal": {
            "frames_emitted": normal_frame_count,
            "secure_frames_admitted": secure_admitted_count,
            "wire_events": normal_wire_events,
            "lru_accepted": normal_lru_accepted,
            "lru_rejected": normal_lru_rejected,
            "gateway_rejection_reasons": rejection_reasons,
        },
        "attack": {
            "requested": attack,
            "attack_at_ms": attack_at_ms,
            "event": attack_event,
        },
        "health": {
            "virtual_card_ready": True,
            "virtual_card_loopback": card.config.loopback,
            "virtual_lru_active": True,
            "virtual_px4_connected": board.connected,
            "hil_sensor_received": board.sensor_count > 0,
            "hil_gps_received": board.gps_count > 0,
            "actuator_feedback_received": len(board.feedback) > 0,
            "disarmed_safe_default": not board.armed,
            "no_real_io": True,
            "closed_cleanly": True,
        },
        "limitations": [
            "VirtualArinc429Card models digital words and timing only; it does not model voltage, impedance, line termination, EMI, or an actual vendor SDK.",
            "VirtualLru uses a synthetic project ICD and does not claim any real aircraft label allocation or LRU behavior.",
            "VirtualPx4Board is a HIL protocol/state model, not a PX4 MCU, sensor driver, PWM, power, or hardware watchdog emulation.",
            "This result is not evidence of a physical ARINC 429 waveform, real LRU test, flight-control hardware HITL, or airworthiness approval.",
        ],
        "trace": trace,
    }
