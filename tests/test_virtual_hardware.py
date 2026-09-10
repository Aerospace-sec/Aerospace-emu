import pytest

from sim.arinc429_lab import encode_word
from sim.virtual_hardware import (
    VirtualArinc429Card,
    VirtualArinc429CardConfig,
    VirtualHardwareError,
    VirtualLru,
    VirtualPx4Board,
)
from sim.virtual_hardware_lab import run_virtual_hardware_lab


def test_virtual_card_loopback_has_explicit_digital_timing() -> None:
    card = VirtualArinc429Card(VirtualArinc429CardConfig(bit_rate_bps=100_000))
    raw = encode_word(0o203, 0, 1_000, 0)

    event = card.submit_tx(tx_channel=0, rx_channel=0, raw_word=raw, requested_ms=0)

    assert event is not None
    assert event.word_duration_ms == pytest.approx(0.36)
    assert card.profile["model"] == "V429-LAB-2T4R"
    assert card.drain_rx()[0].observed_raw_word == raw


def test_virtual_lru_accepts_valid_word_and_rejects_bit_fault() -> None:
    card = VirtualArinc429Card()
    lru = VirtualLru(name="TEST_LRU", accepted_labels=(0o203,))
    raw = encode_word(0o203, 0, 1_000, 0)

    valid_event = card.submit_tx(tx_channel=0, rx_channel=0, raw_word=raw, requested_ms=0)
    valid = lru.consume(valid_event, observed_at_ms=0)
    bad_event = card.submit_tx(
        tx_channel=0,
        rx_channel=0,
        raw_word=raw,
        requested_ms=1,
        bit_flip_mask=1 << 12,
    )
    bad = lru.consume(bad_event, observed_at_ms=1)

    assert valid.accepted
    assert not bad.accepted
    assert bad.reason == "parity"


def test_virtual_card_reports_fifo_overflow() -> None:
    card = VirtualArinc429Card(VirtualArinc429CardConfig(tx_fifo_depth=1))
    raw = encode_word(0o203, 0, 1_000, 0)
    card.submit_tx(tx_channel=0, rx_channel=0, raw_word=raw, requested_ms=0)

    with pytest.raises(VirtualHardwareError, match="tx_fifo_overflow"):
        card.submit_tx(tx_channel=0, rx_channel=0, raw_word=raw, requested_ms=0)

    assert card.overflow_words == 1


def test_virtual_px4_board_is_disarmed_and_watchdog_is_observable() -> None:
    board = VirtualPx4Board(sensor_timeout_ms=10, gps_timeout_ms=20)
    board.connect()
    assert not board.request_arm()
    board.receive_hil_sensor(0, {"xacc": 0.0})
    board.receive_hil_gps(0, {"fix_type": 3})

    healthy = board.step(5)
    timed_out = board.step(25)

    assert not healthy.failsafe
    assert not healthy.armed
    assert timed_out.failsafe
    assert timed_out.reason == "sensor_timeout"


def test_virtual_lab_normal_path_closes_the_full_software_loop() -> None:
    result = run_virtual_hardware_lab(duration_ms=200, step_ms=20, engine="kinematic")

    assert result["normal"]["frames_emitted"] == 44
    assert result["normal"]["secure_frames_admitted"] == 44
    assert result["normal"]["lru_accepted"] == 44
    assert result["normal"]["lru_rejected"] == 0
    assert result["board"]["sensor_count"] > 0
    assert result["board"]["gps_count"] > 0
    assert result["board"]["actuator_feedback_count"] == 11
    assert result["health"]["no_real_io"]
    assert result["health"]["disarmed_safe_default"]


def test_virtual_lab_injection_is_accepted_by_direct_lru_but_blocked_at_gateway() -> None:
    result = run_virtual_hardware_lab(
        duration_ms=200,
        step_ms=20,
        engine="kinematic",
        attack="injection",
        attack_at_ms=100,
    )
    event = result["attack"]["event"]

    assert event["legacy_lru"]["accepted"]
    assert not event["hardened_gateway"]["accepted"]
    assert event["hardened_gateway"]["reason"] == "unauthorized_source"
    assert not event["hardened_physical_output"]


def test_virtual_lab_replay_and_recomputed_parity_tamper_are_blocked() -> None:
    replay = run_virtual_hardware_lab(
        duration_ms=200,
        step_ms=20,
        engine="kinematic",
        attack="replay",
        attack_at_ms=100,
    )["attack"]["event"]
    tamper = run_virtual_hardware_lab(
        duration_ms=200,
        step_ms=20,
        engine="kinematic",
        attack="tamper",
        attack_at_ms=100,
    )["attack"]["event"]

    assert replay["legacy_lru"]["accepted"]
    assert replay["hardened_gateway"]["reason"] == "replay"
    assert tamper["legacy_lru"]["accepted"]
    assert tamper["parity_valid"]
    assert tamper["hardened_gateway"]["reason"] == "authentication"


def test_virtual_lab_rate_mismatch_causes_lru_rejection_and_board_failsafe() -> None:
    result = run_virtual_hardware_lab(
        duration_ms=200,
        step_ms=20,
        engine="kinematic",
        attack="rate_mismatch",
        attack_at_ms=100,
        card_bit_rate_bps=100_000,
        lru_bit_rate_bps=12_500,
    )

    assert result["lru"]["accepted_count"] == 0
    assert result["lru"]["rejected_count"] == 44
    assert result["lru"]["rejection_reasons"] == {"rate_mismatch": 44}
    assert result["board"]["sensor_count"] == 0
    assert result["board"]["failsafe_count"] == 11


def test_virtual_lab_flood_is_rejected_before_hardened_physical_output() -> None:
    result = run_virtual_hardware_lab(
        duration_ms=200,
        step_ms=20,
        engine="kinematic",
        attack="flood",
        attack_at_ms=100,
    )
    event = result["attack"]["event"]

    assert event["legacy_lru_accepted"] == 64
    assert event["hardened_gateway_rejected"] == 64
    assert event["physical_events_submitted"] == 64
    assert event["tx_fifo_overflow"] == 0
    assert not event["hardened_physical_output"]
