"""Deterministic defensive event chain across the synthetic protocol models.

This module carries semantic events only. It never opens a socket, serial port,
radio, ARINC 429 interface, or real aircraft connection.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any, Mapping

from ..arinc429_lab import run_scenarios
from ..atg5g_lab import run_atg_scenarios
from ..serial_lab import run_serial_scenarios
from ..virtual_hardware_lab import run_virtual_hardware_lab


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
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _event(clock: SimulationClock, seq: int, event_type: str, source: str, target: str,
           parent: str | None, status: str, payload: Mapping[str, Any], evidence: list[EvidenceRecord]) -> CausalEvent:
    event_id = f"chain-{seq:03d}-{event_type}"
    evidence_id = f"evidence-{seq:03d}"
    evidence.append(EvidenceRecord(evidence_id, event_id, payload.get("model_ref", "synthetic"), "virtual_model", _digest(payload), "synthetic_model", (event_type, status)))
    return CausalEvent(event_id, event_type, clock.at(seq), seq, source, target, parent, status, dict(payload), (evidence_id,))


def run_cross_model_chain(*, scenario: str = "normal", clock: SimulationClock | None = None) -> dict[str, Any]:
    """Run a 5G-to-PX4 semantic chain with causal links and evidence hashes."""
    if scenario not in {"normal", "injection", "replay", "tamper"}:
        raise ChainValidationError(f"unsupported chain scenario: {scenario}")
    clock = clock or SimulationClock()
    results = {
        "atg": run_atg_scenarios(),
        "serial": run_serial_scenarios(),
        "arinc429": run_scenarios(),
        "hardware": run_virtual_hardware_lab(duration_ms=200, step_ms=20, attack="none" if scenario == "normal" else scenario, attack_at_ms=200),
    }
    evidence: list[EvidenceRecord] = []
    events: list[CausalEvent] = []
    e = _event(clock, 0, "five_g_session_ready", "ATG-UE-001", "ATG-CORE-01", None, "accepted", {"model_ref": "sim.atg5g_lab", "session": "synthetic-atg", "source_result_keys": list(results["atg"])}, evidence); events.append(e)
    e = _event(clock, 1, "n6_packet_observed", "ATG-CORE-01", "N6-GROUND-APP", e.event_id, "accepted", {"model_ref": "sim.atg5g_lab", "path": "N3-to-N6", "payload_class": "synthetic-business-data"}, evidence); events.append(e)
    gateway_status = "rejected" if scenario != "normal" else "accepted"
    e = _event(clock, 2, "cross_domain_gateway_decision", "N6-GROUND-APP", "CROSS-DOMAIN-GATEWAY", e.event_id, gateway_status, {"model_ref": "sim.atg5g_lab", "policy": "aircraft-boundary", "reason": "scenario_negative_validation" if gateway_status == "rejected" else "authorized_semantic_route"}, evidence); events.append(e)
    downstream_status = "blocked" if gateway_status == "rejected" else "accepted"
    e = _event(clock, 3, "serial_bridge_handoff", "CROSS-DOMAIN-GATEWAY", "RS422-AVIONICS-LRU", e.event_id, downstream_status, {"model_ref": "sim.serial_lab", "protocol": "RS-422", "raw_write": False, "reason": "upstream_gateway_rejection" if downstream_status == "blocked" else "synthetic_frame_policy_pass"}, evidence); events.append(e)
    e = _event(clock, 4, "arinc429_semantic_handoff", "RS422-AVIONICS-LRU", "V429-LAB-2T4R", e.event_id, downstream_status, {"model_ref": "sim.arinc429_lab", "protocol": "ARINC-429", "parity_is_not_authentication": True}, evidence); events.append(e)
    e = _event(clock, 5, "virtual_lru_admission", "V429-LAB-2T4R", "SYNTHETIC_AIR_DATA_LRU", e.event_id, downstream_status, {"model_ref": "sim.virtual_hardware", "physical_output": False, "reason": "upstream_chain_blocked" if downstream_status == "blocked" else "synthetic_icd_acceptance"}, evidence); events.append(e)
    e = _event(clock, 6, "px4_hil_feedback", "SYNTHETIC_AIR_DATA_LRU", "PX4-FMU-VIRTUAL", e.event_id, downstream_status, {"model_ref": "sim.virtual_hardware", "armed": False, "physical_output": False, "safe_default": True}, evidence); events.append(e)
    return {"scenario": scenario, "clock": asdict(clock), "events": [asdict(item) for item in events], "evidence": [asdict(item) for item in evidence], "terminal_status": downstream_status, "physical_output": False, "model_results": {key: list(value) for key, value in results.items()}}
