from sim.arinc825_lab import Arinc825Bus, CanFrame, CanNode, run_scenarios


def test_can_arbitration_lower_identifier_wins():
    bus = Arinc825Bus((CanNode("a", "power"), CanNode("b", "cabin")))
    winner = bus.arbitrate((CanFrame(0x300, "b", 1, 2, 0), CanFrame(0x100, "a", 1, 2, 0)))
    assert winner is not None and winner.frame.arbitration_id == 0x100


def test_can_rejects_unauthorized_replay_and_oversize():
    result = run_scenarios()["attacks"]
    assert result["unauthorized_node"]["reason"] == "unauthorized_node"
    assert result["replay"]["reason"] == "replay"
    assert result["oversize"]["reason"] == "payload_too_large"


def test_can_error_passive_and_load_are_observable():
    result = run_scenarios()
    assert result["attacks"]["error_injection"]["state"] == "error_passive"
    assert result["normal"]["load_percent"] > 0
