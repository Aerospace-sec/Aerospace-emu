from sim.afdx_lab import AfdxSimulator, AfdxVirtualLink, run_scenarios


def test_afdx_bag_and_frame_policy_are_deterministic():
    link = AfdxVirtualLink(1, "VL", "ES", ("RX",), bag_ms=4, max_frame_bytes=64, redundant=False)
    simulator = AfdxSimulator((link,))
    assert simulator.schedule(1, 1, 0, 32).reason == "scheduled"
    assert simulator.schedule(1, 2, 1, 32).reason == "bag_violation"
    assert simulator.schedule(1, 3, 4, 65).reason == "frame_too_large"


def test_afdx_redundancy_delivers_once_and_deduplicates():
    link = AfdxVirtualLink(1, "VL", "ES", ("RX",), redundant=True)
    simulator = AfdxSimulator((link,))
    copies = simulator.transmit_redundant(simulator.schedule(1, 1, 0, 32))
    assert {frame.network for frame in copies} == {"A", "B"}
    assert simulator.receive(copies[0]).reason == "delivered"
    assert simulator.receive(copies[1]).reason == "redundant_duplicate"


def test_afdx_scenarios_capture_negative_results():
    result = run_scenarios()
    assert result["normal"]["copies"] == 2
    assert result["attacks"]["bag_violation"]["decision"]["reason"] == "bag_violation"
    assert result["attacks"]["oversize"]["decision"]["reason"] == "frame_too_large"
    assert result["attacks"]["redundant_duplicate"]["results"][1]["reason"] == "redundant_duplicate"
