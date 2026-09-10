from sim.civil_aviation_platform import (
    CivilScenario,
    KinematicCivilModel,
    build_gateway,
    dependency_inventory,
    run_platform,
)


def test_scenario_has_civil_flight_phases_and_monotonic_position() -> None:
    model = KinematicCivilModel(CivilScenario())
    states = [model.step(timestamp_ms) for timestamp_ms in (0, 20_000, 60_000, 120_000, 180_000)]

    assert [state.phase for state in states] == ["takeoff_climb", "climb", "cruise", "descent", "approach"]
    assert states[-1].altitude_ft < states[2].altitude_ft
    assert states[-1].longitude_deg > states[0].longitude_deg


def test_platform_emits_and_admits_normal_arinc_frames() -> None:
    result = run_platform(duration_ms=3_000, step_ms=1_000, engine="kinematic")

    assert result["normal_traffic"]["frames_emitted"] == 16
    assert result["normal_traffic"]["frames_admitted"] == 16
    assert result["normal_traffic"]["frames_rejected"] == 0
    assert result["integrations"]["network_opened"] is False


def test_platform_records_unauthorized_injection_before_and_after() -> None:
    result = run_platform(
        duration_ms=3_000,
        step_ms=1_000,
        engine="kinematic",
        attack="injection",
        attack_at_ms=2_000,
    )
    event = result["attack"]["event"]

    assert event["legacy"]["accepted"] is True
    assert event["hardened"]["accepted"] is False
    assert event["hardened"]["reason"] == "unauthorized_source"


def test_platform_records_replay_and_tamper_controls() -> None:
    replay = run_platform(
        duration_ms=3_000,
        step_ms=1_000,
        engine="kinematic",
        attack="replay",
        attack_at_ms=2_000,
    )["attack"]["event"]
    tamper = run_platform(
        duration_ms=3_000,
        step_ms=1_000,
        engine="kinematic",
        attack="tamper",
        attack_at_ms=2_000,
    )["attack"]["event"]

    assert replay["legacy"]["accepted"] is True
    assert replay["hardened"]["reason"] == "replay"
    assert tamper["legacy"]["accepted"] is True
    assert tamper["hardened"]["reason"] == "authentication"


def test_auto_engine_reports_fallback_when_jsbsim_is_not_available() -> None:
    result = run_platform(duration_ms=1_000, step_ms=1_000, engine="auto")

    assert result["engine"]["selected"] in {"kinematic", "jsbsim"}
    if not result["integrations"]["jsbsim_python"]:
        assert result["engine"]["selected"] == "kinematic"
