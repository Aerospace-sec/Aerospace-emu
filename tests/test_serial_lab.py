from dataclasses import replace

import pytest

from sim.serial_lab import (
    RS232_MAINTENANCE_PROFILE,
    RS422_AVIONICS_PROFILE,
    SecureSerialGateway,
    SerialLink,
    SerialReceiver,
    SerialLabError,
    decode_native_frame,
    encode_native_frame,
    make_secure_envelope,
    run_serial_scenarios,
    tamper_native_payload,
)


def test_native_frame_round_trip_and_crc_detection() -> None:
    raw = encode_native_frame("AIR_DATA_UPDATE", 7, {"altitude_ft": 30_000, "airspeed_kt": 440})
    decoded = decode_native_frame(raw)

    assert decoded.message_type == "AIR_DATA_UPDATE"
    assert decoded.sequence == 7
    assert decoded.payload["altitude_ft"] == 30_000
    assert decoded.crc_ok
    assert not decode_native_frame(raw[:-1] + bytes((raw[-1] ^ 1,))).crc_ok


def test_recomputed_crc_tamper_is_valid_transport_frame() -> None:
    raw = encode_native_frame("AIR_DATA_UPDATE", 1, {"altitude_ft": 30_000, "airspeed_kt": 440})
    tampered = tamper_native_payload(raw, {"altitude_ft": 500, "airspeed_kt": 80})

    assert decode_native_frame(tampered).crc_ok
    assert decode_native_frame(tampered).payload["altitude_ft"] == 500


def test_profiles_capture_rs232_and_rs422_full_duplex_boundaries() -> None:
    assert not RS232_MAINTENANCE_PROFILE.differential
    assert RS232_MAINTENANCE_PROFILE.full_duplex
    assert RS232_MAINTENANCE_PROFILE.independent_signal_pairs == 1
    assert RS422_AVIONICS_PROFILE.differential
    assert RS422_AVIONICS_PROFILE.full_duplex
    assert RS422_AVIONICS_PROFILE.independent_signal_pairs == 2


def test_full_duplex_directions_have_independent_digital_clocks() -> None:
    link = SerialLink(RS422_AVIONICS_PROFILE)
    raw = encode_native_frame("AIR_DATA_UPDATE", 1, {"altitude_ft": 30_000, "airspeed_kt": 440})

    forward = link.transmit(raw, direction="A->B", requested_ms=0)
    reverse = link.transmit(raw, direction="B->A", requested_ms=0)

    assert forward.start_ms == pytest.approx(0.0)
    assert reverse.start_ms == pytest.approx(0.0)


def test_native_receiver_has_no_source_authentication() -> None:
    receiver = SerialReceiver(name="FMS", accepted_types={"AIR_DATA_UPDATE"})
    raw = encode_native_frame("AIR_DATA_UPDATE", 900, {"altitude_ft": 500, "airspeed_kt": 80, "valid": True})

    result = receiver.receive(raw, now_ms=100)

    assert result.accepted
    assert result.effect == "air_data_updated:500ft@100ms"


def test_secure_gateway_rejects_unknown_source_and_modified_native_frame() -> None:
    gateway = SecureSerialGateway(
        secrets={"adc_lru": b"key"},
        source_roles={"adc_lru": "avionics_lru"},
        allowed_channels={"RS422-AVIONICS-LRU"},
    )
    raw = encode_native_frame("AIR_DATA_UPDATE", 1, {"altitude_ft": 30_000, "airspeed_kt": 440})
    attacker = make_secure_envelope(
        source="attacker",
        role="avionics_lru",
        channel="RS422-AVIONICS-LRU",
        timestamp_ms=0,
        sequence=1,
        message_type="AIR_DATA_UPDATE",
        native_raw=raw,
        secret=b"wrong",
    )
    assert gateway.admit(attacker, now_ms=0).reason == "unauthorized_source"

    legitimate = make_secure_envelope(
        source="adc_lru",
        role="avionics_lru",
        channel="RS422-AVIONICS-LRU",
        timestamp_ms=0,
        sequence=1,
        message_type="AIR_DATA_UPDATE",
        native_raw=raw,
        secret=b"key",
    )
    modified = replace(
        legitimate,
        native_raw=tamper_native_payload(raw, {"altitude_ft": 500, "airspeed_kt": 80}),
    )
    assert gateway.admit(modified, now_ms=0).reason == "authentication"


def test_secure_gateway_rejects_replay_and_airborne_maintenance() -> None:
    gateway = SecureSerialGateway(
        secrets={"approved_gse": b"gse"},
        source_roles={"approved_gse": "approved_gse"},
        allowed_channels={"RS232-GSE-MAINT"},
    )
    raw = encode_native_frame("LOAD_CONFIGURATION", 1, {"calibration_id": "BASE", "offset_ft": 0})
    envelope = make_secure_envelope(
        source="approved_gse",
        role="approved_gse",
        channel="RS232-GSE-MAINT",
        timestamp_ms=0,
        sequence=1,
        message_type="LOAD_CONFIGURATION",
        native_raw=raw,
        secret=b"gse",
    )
    assert gateway.admit(envelope, now_ms=0).accepted
    assert gateway.admit(envelope, now_ms=10).reason == "replay"

    airborne = SecureSerialGateway(
        secrets={"approved_gse": b"gse"},
        source_roles={"approved_gse": "approved_gse"},
        allowed_channels={"RS232-GSE-MAINT"},
        ground_state="AIRBORNE",
        maintenance_session=False,
    )
    assert airborne.admit(envelope, now_ms=0).reason == "maintenance_not_available"


def test_serial_scenarios_prove_attack_and_hardening_results() -> None:
    results = run_serial_scenarios()
    attacks = results["attacks"]

    assert results["normal"]["rs422"]["gateway"]["accepted"]
    assert results["normal"]["rs422"]["receiver"]["accepted"]
    assert attacks["physical_tap_and_capture"]["confidentiality"] is False
    assert attacks["injection"]["native"]["accepted"]
    assert attacks["injection"]["hardened"]["reason"] == "unauthorized_source"
    assert attacks["tamper_recomputed_crc"]["native"]["accepted"]
    assert attacks["tamper_recomputed_crc"]["crc_valid_after_tamper"]
    assert attacks["tamper_recomputed_crc"]["hardened"]["reason"] == "authentication"
    assert attacks["replay"]["first_hardened"]["accepted"]
    assert attacks["replay"]["native"]["accepted"]
    assert attacks["replay"]["hardened"]["reason"] == "replay"
    assert attacks["airborne_maintenance"]["direct_native"]["accepted"]
    assert attacks["airborne_maintenance"]["hardened_gateway"]["reason"] == "maintenance_not_available"
    assert results["normal"]["rs232"]["configuration_mismatch"]["sender_baud_rate"] == 9_600
    assert results["normal"]["rs232"]["configuration_mismatch"]["reason"] == "rate_mismatch"
    assert attacks["flood"]["native_attacker_accepted"] == 64
    assert attacks["flood"]["hardened_attacker_rejected"] == 64
    assert attacks["flood"]["native_deadline_missed"]
    assert not attacks["flood"]["hardened_deadline_missed"]


def test_invalid_serial_direction_is_rejected() -> None:
    link = SerialLink(RS232_MAINTENANCE_PROFILE)
    raw = encode_native_frame("BITE_LOG_READ", 1, {})

    with pytest.raises(SerialLabError):
        link.transmit(raw, direction="invalid", requested_ms=0)
