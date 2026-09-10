from sim.atg5g_lab import run_atg_scenarios


def test_normal_registration_and_business_paths_are_available() -> None:
    results = run_atg_scenarios()

    registration = results["air_interface"]["trusted_registration"]
    assert registration["result"] == "registered"
    assert registration["aka_success"]
    assert registration["security_mode_complete"]
    assert registration["user_plane_ready"]
    assert results["normal"]["telemetry_hardened"]["accepted"]
    assert results["normal"]["maintenance_hardened"]["accepted"]


def test_interference_and_rogue_cell_states_are_explicit() -> None:
    results = run_atg_scenarios()
    interference = results["attacks"]["interference"]
    rogue = results["attacks"]["rogue_cell"]

    assert not interference["service_available"]
    assert interference["registration"]["result"] == "registration_timeout"
    assert rogue["hardened"]["result"] == "rogue_cell_rejected"
    assert rogue["hardened"]["pre_security_metadata_visible"]
    assert rogue["legacy"]["result"] == "camped_untrusted_cell"
    assert not rogue["hardened"]["user_plane_ready"]
    assert not rogue["legacy"]["user_plane_ready"]


def test_signaling_rate_limit_reduces_context_pressure() -> None:
    storm = run_atg_scenarios()["attacks"]["signaling_storm"]

    assert storm["attempts"] == 50
    assert storm["native_contexts_allocated"] == 50
    assert storm["hardened_contexts_allocated"] == 10
    assert storm["hardened_rejected"] == 40
    assert storm["hardened_reason"] == "signaling_rate_limit"


def test_cabin_to_avionics_boundary_is_blocked_before_delivery() -> None:
    boundary = run_atg_scenarios()["attacks"]["cabin_to_avionics_boundary"]

    assert boundary["native"]["accepted"]
    assert boundary["hardened"]["reason"] == "cabin_to_avionics_boundary"
    assert boundary["native_state"]["avionics_commands"] == ["SET_FLIGHT_DISPLAY_MODE"]
    assert boundary["hardened_state"]["avionics_commands"] == []


def test_user_plane_tamper_and_replay_are_rejected() -> None:
    attacks = run_atg_scenarios()["attacks"]
    tamper = attacks["data_tamper"]
    replay = attacks["data_replay"]

    assert tamper["native"]["accepted"]
    assert tamper["hardened"]["reason"] == "authentication"
    assert replay["native"]["accepted"]
    assert replay["first_hardened"]["accepted"]
    assert not replay["replayed_hardened"]["accepted"]
    assert replay["replayed_hardened"]["reason"] == "replay"


def test_n3_session_and_qfi_binding_rejects_forged_context() -> None:
    binding = run_atg_scenarios()["attacks"]["session_binding"]

    assert binding["teid_forgery"]["native"]["accepted"]
    assert binding["teid_forgery"]["hardened"]["reason"] == "session_binding"
    assert binding["qfi_forgery"]["native"]["accepted"]
    assert binding["qfi_forgery"]["hardened"]["reason"] == "qfi_binding"


def test_oam_route_change_requires_authorization_and_preserves_boundary() -> None:
    oam = run_atg_scenarios()["attacks"]["oam_route_change"]

    assert oam["native"]["accepted"]
    assert not oam["hardened_unauthorized"]["accepted"]
    assert oam["hardened_unauthorized"]["reason"] == "oam_rbac"
    assert oam["hardened_authorized"]["accepted"]
    assert not oam["hardened_authorized"]["config"]["cabin_to_avionics_route"]
    assert oam["hardened_authorized"]["config"]["slice_isolation"]


def test_capture_model_distinguishes_metadata_transport_and_payload() -> None:
    captures = run_atg_scenarios()["captures"]

    assert captures["uu"]["broadcast_sib_visible"]
    assert captures["uu"]["pre_security_registration_metadata_visible"]
    assert captures["n3_n6"]["gtp_u_outer_headers_visible"]
    assert captures["n3_n6"]["teid_visible_to_transport_observer"]
    assert captures["n3_n6"]["application_payload_readable_without_app_tls"]
    assert not captures["n3_n6"]["application_payload_readable_with_app_tls"]
