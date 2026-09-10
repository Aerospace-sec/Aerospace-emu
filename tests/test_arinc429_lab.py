from dataclasses import replace

import pytest

from sim.arinc429_lab import (
    AUTHORIZED_SOURCE,
    LAB_SECRET,
    TEST_LABEL,
    BusFrame,
    LabelPolicy,
    LegacyReceiver,
    SecureGateway,
    decode_word,
    encode_word,
    make_secure_frame,
    run_scenarios,
    tamper_data,
    timing_lock,
)


def gateway() -> SecureGateway:
    return SecureGateway(
        LAB_SECRET,
        {AUTHORIZED_SOURCE},
        {TEST_LABEL: LabelPolicy(0, 10_000, max_delta=2_000)},
    )


def test_encode_decode_preserves_fields_and_odd_parity() -> None:
    raw = encode_word(TEST_LABEL, 2, 123_456, 1)
    decoded = decode_word(raw)

    assert decoded.label == TEST_LABEL
    assert decoded.sdi == 2
    assert decoded.data == 123_456
    assert decoded.ssm == 1
    assert decoded.parity_ok
    assert raw.bit_count() % 2 == 1


def test_parity_detects_single_bit_error() -> None:
    raw = encode_word(TEST_LABEL, 0, 1000, 0)
    assert not decode_word(raw ^ (1 << 12)).parity_ok


def test_parity_cannot_detect_even_number_of_bit_flips() -> None:
    raw = encode_word(TEST_LABEL, 0, 1000, 0)

    assert decode_word(raw ^ (1 << 12) ^ (1 << 13)).parity_ok


def test_recomputed_parity_tamper_bypasses_legacy_receiver() -> None:
    raw = encode_word(TEST_LABEL, 0, 1000, 0)
    tampered = tamper_data(raw, 1001)

    result = LegacyReceiver({TEST_LABEL}).receive(BusFrame(0, "attacker", tampered))

    assert result.accepted
    assert decode_word(tampered).parity_ok


def test_gateway_rejects_unauthorized_injection() -> None:
    frame = BusFrame(0, "attacker", encode_word(TEST_LABEL, 0, 1000, 0))

    result = gateway().admit(frame, 0)

    assert not result.accepted
    assert result.reason == "unauthorized_source"


def test_gateway_rejects_replay_and_stale_frame() -> None:
    frame = make_secure_frame(
        0,
        AUTHORIZED_SOURCE,
        encode_word(TEST_LABEL, 0, 1000, 0),
        1,
        LAB_SECRET,
    )
    secure_gateway = gateway()

    assert secure_gateway.admit(frame, 0).accepted
    assert secure_gateway.admit(frame, 10).reason == "replay"
    assert gateway().admit(frame, 500).reason == "stale"


def test_gateway_rejects_modified_word_with_original_mac() -> None:
    original = make_secure_frame(
        0,
        AUTHORIZED_SOURCE,
        encode_word(TEST_LABEL, 0, 1000, 0),
        1,
        LAB_SECRET,
    )
    modified = replace(original, raw_word=tamper_data(original.raw_word, 1001))

    result = gateway().admit(modified, 0)

    assert not result.accepted
    assert result.reason == "authentication"


def test_gateway_supports_source_specific_keys() -> None:
    source_keys = {
        AUTHORIZED_SOURCE: LAB_SECRET,
        "second_authorized_source": b"second-source-key",
    }
    secure_gateway = SecureGateway(
        source_keys,
        source_keys,
        {TEST_LABEL: LabelPolicy(0, 10_000)},
    )
    frame = make_secure_frame(
        0,
        AUTHORIZED_SOURCE,
        encode_word(TEST_LABEL, 0, 1000, 0),
        1,
        b"second-source-key",
    )

    result = secure_gateway.admit(frame, 0)

    assert not result.accepted
    assert result.reason == "authentication"


def test_gateway_rejects_out_of_range_data() -> None:
    frame = make_secure_frame(
        0,
        AUTHORIZED_SOURCE,
        encode_word(TEST_LABEL, 0, 10_001, 0),
        1,
        LAB_SECRET,
    )

    result = gateway().admit(frame, 0)

    assert not result.accepted
    assert result.reason == "semantic_range"


def test_experiment_results_show_before_after_difference() -> None:
    results = run_scenarios()

    assert results["injection"]["legacy"]["accepted"]
    assert not results["injection"]["hardened"]["accepted"]
    assert results["parity_recomputed_tamper"]["parity_valid_after_tamper"]
    assert results["parity_recomputed_tamper"]["legacy"]["accepted"]
    assert not results["parity_recomputed_tamper"]["hardened"]["accepted"]
    assert results["flood"]["legacy_deadline_missed"]
    assert not results["flood"]["hardened_deadline_missed"]


@pytest.mark.parametrize("bit_rate", [12_500, 100_000])
def test_word_serialization_supports_both_defined_rates(bit_rate: int) -> None:
    from sim.arinc429_lab import serialize_frames

    frame = BusFrame(0, AUTHORIZED_SOURCE, encode_word(TEST_LABEL, 0, 1000, 0))
    transmission = serialize_frames([frame], bit_rate)[0]

    assert transmission.end_ms == pytest.approx(32_000 / bit_rate)


def test_distinct_nominal_rates_do_not_lock_in_rate_model() -> None:
    assert timing_lock(100_000, 100_000)
    assert timing_lock(12_500, 12_500)
    assert not timing_lock(100_000, 12_500)


def test_experiment_reports_rate_mismatch_and_parity_baseline() -> None:
    results = run_scenarios()

    assert not results["rate_mismatch"]["receiver_valid_word"]
    assert not results["parity_baseline"]["single_bit_flip_parity_ok"]
