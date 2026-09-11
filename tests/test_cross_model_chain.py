import json
from sim.twin.chain import SimulationClock, run_cross_model_chain


def test_cross_model_chain_has_monotonic_clock_and_causal_links():
    result = run_cross_model_chain(clock=SimulationClock(epoch_ms=10_000, step_ms=25))
    events = result["events"]
    assert [event["timestamp_ms"] for event in events] == [10_000 + i * 25 for i in range(7)]
    assert events[0]["parent_event_id"] is None
    for previous, current in zip(events, events[1:]):
        assert current["parent_event_id"] == previous["event_id"]
        assert current["evidence_ids"]
    assert result["terminal_status"] == "accepted"
    assert result["physical_output"] is False


def test_negative_chain_blocks_downstream_without_physical_output():
    result = run_cross_model_chain(scenario="injection")
    statuses = [event["status"] for event in result["events"]]
    assert statuses[:3] == ["accepted", "accepted", "rejected"]
    assert statuses[3:] == ["blocked"] * 4
    assert result["physical_output"] is False
    assert all(record["source"] == "virtual_model" for record in result["evidence"])
    assert all(len(record["digest_sha256"]) == 64 for record in result["evidence"])

def test_each_hop_carries_semantic_envelope_and_policy_decision():
    result = run_cross_model_chain(scenario="injection")
    assert len(result["decisions"]) == len(result["events"]) == 7
    for event, decision in zip(result["events"], result["decisions"]):
        envelope = event["payload"]["envelope"]
        assert envelope["simulation_only"] is True
        assert len(envelope["payload_digest"]) == 64
        assert event["payload"]["policy_decision"]["event_id"] == event["event_id"]
        assert decision["input_digest"] == envelope["payload_digest"]
    assert result["decisions"][2]["decision"] == "denied"
    assert result["decisions"][3]["decision"] == "blocked"


def test_chain_json_is_stable_and_replayable():
    first = run_cross_model_chain()
    second = run_cross_model_chain()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
