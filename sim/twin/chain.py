"""Deterministic defensive event chain across the synthetic protocol models."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping

from ..arinc429_lab import run_scenarios
from ..atg5g_lab import run_atg_scenarios
from ..serial_lab import run_serial_scenarios
from ..virtual_hardware_lab import run_virtual_hardware_lab
from .message import MessageEnvelope, PolicyDecision


class ChainValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SimulationClock:
    epoch_ms: int = 0
    step_ms: int = 100

    def at(self, offset: int) -> int:
        if offset < 0:
            raise ChainValidationError("clock offset must not be negative")
        return self.epoch_ms + offset * self.step_ms


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    event_id: str
    model_ref: str
    source: str
    digest_sha256: str
    evidence_level: str
    claims: tuple[str, ...]


@dataclass(frozen=True)
class CausalEvent:
    event_id: str
    event_type: str
    timestamp_ms: int
    sequence: int
    source_device: str
    target_device: str
    parent_event_id: str | None
    status: str
    payload: Mapping[str, Any]
    evidence_ids: tuple[str, ...]


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _event(clock: SimulationClock, seq: int, event_type: str, envelope: MessageEnvelope,
           decision: PolicyDecision, model_ref: str, evidence: list[EvidenceRecord],
           extra: Mapping[str, Any] | None = None) -> CausalEvent:
    event_id = f"chain-{seq:03d}-{event_type}"
    evidence_id = f"evidence-{seq:03d}"
    payload = {"model_ref": model_ref, "envelope": envelope.as_dict(), "policy_decision": decision.as_dict(), **(dict(extra or {}))}
    evidence.append(EvidenceRecord(evidence_id, event_id, model_ref, "virtual_model", _digest(payload), "synthetic_model", (event_type, decision.decision)))
    status = {"allowed": "accepted", "denied": "rejected", "blocked": "blocked"}[decision.decision]
    return CausalEvent(event_id, event_type, clock.at(seq), seq, envelope.source_device, envelope.target_device, envelope.parent_event_id, status, payload, (evidence_id,))


def _forward(envelope: MessageEnvelope, *, event_id: str, timestamp_ms: int,
             source_device: str, source_interface: str, target_device: str,
             target_interface: str, protocol: str, authorization: str) -> MessageEnvelope:
    return envelope.with_decision(parent_event_id=event_id, sim_time_ms=timestamp_ms,
                                  source_device=source_device, source_interface=source_interface,
                                  target_device=target_device, target_interface=target_interface,
                                  authorization=authorization)


def run_cross_model_chain(*, scenario: str = "normal", clock: SimulationClock | None = None) -> dict[str, Any]:
    """Run a 5G-to-PX4 semantic chain with per-hop envelopes and policies."""
    if scenario not in {"normal", "injection", "replay", "tamper"}:
        raise ChainValidationError(f"unsupported chain scenario: {scenario}")
    clock = clock or SimulationClock()
    results = {"atg": run_atg_scenarios(), "serial": run_serial_scenarios(), "arinc429": run_scenarios(), "hardware": run_virtual_hardware_lab(duration_ms=200, step_ms=20, attack="none" if scenario == "normal" else scenario, attack_at_ms=200)}
    evidence: list[EvidenceRecord] = []
    events: list[CausalEvent] = []
    decisions: list[PolicyDecision] = []

    envelope = MessageEnvelope.create(message_id="msg-001", correlation_id="chain-001", parent_event_id=None, sim_time_ms=clock.at(0), source_device="ATG-UE-001", source_interface="nr0", target_device="ATG-CORE-01", target_interface="n3", protocol="5g-n3", message_class="synthetic-business-data", semantic_fields={"session": "synthetic-atg"}, authenticity="verified", freshness="fresh", authorization="allowed", safety_impact="operational")
    decision = PolicyDecision("atg-session-policy", "allowed", "synthetic_session_bound", envelope.payload_digest, envelope.payload_digest, "chain-000-five_g_session_ready"); decisions.append(decision)
    event = _event(clock, 0, "five_g_session_ready", envelope, decision, "sim.atg5g_lab", evidence, {"source_result_keys": list(results["atg"]) }); events.append(event)

    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(1), source_device="ATG-CORE-01", source_interface="n6", target_device="N6-GROUND-APP", target_interface="n6", protocol="n6", authorization="allowed")
    decision = PolicyDecision("n6-egress-policy", "allowed", "n6_route_bound", envelope.payload_digest, envelope.payload_digest, "chain-001-n6_packet_observed"); decisions.append(decision)
    event = _event(clock, 1, "n6_packet_observed", envelope, decision, "sim.atg5g_lab", evidence, {"path": "N3-to-N6"}); events.append(event)

    allowed = scenario == "normal"
    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(2), source_device="N6-GROUND-APP", source_interface="n6", target_device="CROSS-DOMAIN-GATEWAY", target_interface="ingress", protocol="cross-domain", authorization="allowed" if allowed else "denied")
    envelope = envelope.with_decision(authenticity="verified" if allowed else "rejected", freshness="fresh" if allowed else ("replay" if scenario == "replay" else "stale" if scenario == "tamper" else "fresh"))
    decision = PolicyDecision("aircraft-boundary-policy", "allowed" if allowed else "denied", "authorized_semantic_route" if allowed else "scenario_negative_validation", envelope.payload_digest, envelope.payload_digest, "chain-002-cross_domain_gateway_decision"); decisions.append(decision)
    event = _event(clock, 2, "cross_domain_gateway_decision", envelope, decision, "sim.atg5g_lab", evidence, {"policy": "aircraft-boundary"}); events.append(event)

    auth_status = "allowed" if allowed else "denied"
    decision_status = "allowed" if allowed else "blocked"
    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(3), source_device="CROSS-DOMAIN-GATEWAY", source_interface="egress", target_device="RS422-AVIONICS-LRU", target_interface="rs422", protocol="rs-422", authorization=auth_status)
    decision = PolicyDecision("serial-bridge-policy", decision_status, "synthetic_frame_policy_pass" if allowed else "upstream_gateway_rejection", envelope.payload_digest, envelope.payload_digest, "chain-003-serial_bridge_handoff"); decisions.append(decision)
    event = _event(clock, 3, "serial_bridge_handoff", envelope, decision, "sim.serial_lab", evidence, {"raw_write": False}); events.append(event)

    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(4), source_device="RS422-AVIONICS-LRU", source_interface="rs422", target_device="V429-LAB-2T4R", target_interface="ch-a", protocol="arinc-429", authorization=auth_status)
    decision = PolicyDecision("arinc429-ingress-policy", decision_status, "parity_and_semantic_policy_pass" if allowed else "upstream_chain_blocked", envelope.payload_digest, envelope.payload_digest, "chain-004-arinc429_semantic_handoff"); decisions.append(decision)
    event = _event(clock, 4, "arinc429_semantic_handoff", envelope, decision, "sim.arinc429_lab", evidence, {"parity_is_not_authentication": True}); events.append(event)

    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(5), source_device="V429-LAB-2T4R", source_interface="ch-a", target_device="SYNTHETIC_AIR_DATA_LRU", target_interface="arinc-in", protocol="arinc-429", authorization=auth_status)
    decision = PolicyDecision("lru-admission-policy", decision_status, "synthetic_icd_acceptance" if allowed else "upstream_chain_blocked", envelope.payload_digest, envelope.payload_digest, "chain-005-virtual_lru_admission"); decisions.append(decision)
    event = _event(clock, 5, "virtual_lru_admission", envelope, decision, "sim.virtual_hardware", evidence, {"physical_output": False}); events.append(event)

    envelope = _forward(envelope, event_id=event.event_id, timestamp_ms=clock.at(6), source_device="SYNTHETIC_AIR_DATA_LRU", source_interface="arinc-in", target_device="PX4-FMU-VIRTUAL", target_interface="hil", protocol="px4-hil", authorization=auth_status)
    decision = PolicyDecision("px4-safe-output-policy", decision_status, "safe_default_hil_feedback" if allowed else "upstream_chain_blocked", envelope.payload_digest, envelope.payload_digest, "chain-006-px4_hil_feedback"); decisions.append(decision)
    event = _event(clock, 6, "px4_hil_feedback", envelope, decision, "sim.virtual_hardware", evidence, {"armed": False, "physical_output": False, "safe_default": True}); events.append(event)

    return {"scenario": scenario, "clock": asdict(clock), "events": [asdict(item) for item in events], "decisions": [item.as_dict() for item in decisions], "evidence": [asdict(item) for item in evidence], "terminal_status": "accepted" if allowed else "blocked", "physical_output": False, "model_results": {key: list(value) for key, value in results.items()}}
