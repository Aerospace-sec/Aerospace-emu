"""Semantic cross-protocol message and policy contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any, Mapping


class MessageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MessageEnvelope:
    message_id: str
    correlation_id: str
    parent_event_id: str | None
    sim_time_ms: int
    source_device: str
    source_interface: str
    target_device: str
    target_interface: str
    protocol: str
    message_class: str
    payload_digest: str
    semantic_fields: Mapping[str, Any]
    authenticity: str = "unknown"
    freshness: str = "unknown"
    authorization: str = "unknown"
    safety_impact: str = "diagnostic"
    simulation_only: bool = True

    def __post_init__(self) -> None:
        if not self.simulation_only:
            raise MessageValidationError("cross-model messages must be simulation_only")
        if self.sim_time_ms < 0 or not self.message_id or not self.correlation_id:
            raise MessageValidationError("message identity and simulation time are required")
        if self.authenticity not in {"unknown", "verified", "rejected"}:
            raise MessageValidationError("invalid authenticity state")
        if self.freshness not in {"unknown", "fresh", "stale", "replay"}:
            raise MessageValidationError("invalid freshness state")
        if self.authorization not in {"unknown", "allowed", "denied"}:
            raise MessageValidationError("invalid authorization state")
        if self.safety_impact not in {"none", "diagnostic", "operational"}:
            raise MessageValidationError("invalid safety impact")

    @classmethod
    def create(cls, *, message_id: str, correlation_id: str, parent_event_id: str | None,
               sim_time_ms: int, source_device: str, source_interface: str,
               target_device: str, target_interface: str, protocol: str,
               message_class: str, semantic_fields: Mapping[str, Any],
               authenticity: str = "unknown", freshness: str = "unknown",
               authorization: str = "unknown", safety_impact: str = "diagnostic") -> "MessageEnvelope":
        digest = hashlib.sha256(json.dumps(dict(semantic_fields), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(message_id, correlation_id, parent_event_id, sim_time_ms, source_device, source_interface, target_device, target_interface, protocol, message_class, digest, dict(semantic_fields), authenticity, freshness, authorization, safety_impact)

    def with_decision(self, *, authenticity: str | None = None, freshness: str | None = None,
                      authorization: str | None = None, parent_event_id: str | None = None,
                      source_device: str | None = None, source_interface: str | None = None,
                      target_device: str | None = None, target_interface: str | None = None,
                      sim_time_ms: int | None = None) -> "MessageEnvelope":
        return replace(self, authenticity=authenticity or self.authenticity, freshness=freshness or self.freshness,
                       authorization=authorization or self.authorization,
                       parent_event_id=parent_event_id if parent_event_id is not None else self.parent_event_id,
                       source_device=source_device or self.source_device, source_interface=source_interface or self.source_interface,
                       target_device=target_device or self.target_device, target_interface=target_interface or self.target_interface,
                       sim_time_ms=self.sim_time_ms if sim_time_ms is None else sim_time_ms)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyDecision:
    policy_id: str
    decision: str
    reason: str
    input_digest: str
    output_digest: str
    event_id: str

    def __post_init__(self) -> None:
        if self.decision not in {"allowed", "denied", "blocked"}:
            raise MessageValidationError("invalid policy decision")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
