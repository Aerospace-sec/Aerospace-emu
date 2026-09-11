"""Deterministic, simulation-only ARINC 664/AFDX virtual-link model.

The model covers VL scheduling, BAG enforcement, frame-size policy and dual
network duplicate suppression. It does not open Ethernet sockets or emulate
AFDX electrical hardware.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


class AfdxError(ValueError):
    pass


@dataclass(frozen=True)
class AfdxVirtualLink:
    vl_id: int
    name: str
    source: str
    destinations: tuple[str, ...]
    bag_ms: int = 4
    max_frame_bytes: int = 512
    redundant: bool = True

    def __post_init__(self) -> None:
        if self.vl_id <= 0 or not self.name or not self.source or not self.destinations:
            raise AfdxError("VL identity and endpoints are required")
        if self.bag_ms <= 0 or self.max_frame_bytes <= 0:
            raise AfdxError("BAG and maximum frame size must be positive")


@dataclass(frozen=True)
class AfdxFrame:
    vl_id: int
    sequence: int
    timestamp_ms: int
    payload_bytes: int
    network: str = "A"


@dataclass(frozen=True)
class AfdxDecision:
    accepted: bool
    reason: str
    frame: AfdxFrame


@dataclass(frozen=True)
class AfdxRun:
    configuration: dict[str, object]
    normal: dict[str, object]
    attacks: dict[str, dict[str, object]]
    limitations: tuple[str, ...]


class AfdxSimulator:
    def __init__(self, links: Iterable[AfdxVirtualLink]) -> None:
        self.links = {link.vl_id: link for link in links}
        if not self.links:
            raise AfdxError("at least one virtual link is required")
        self.next_allowed: dict[int, int] = {link.vl_id: 0 for link in self.links.values()}
        self.seen: set[tuple[int, int]] = set()

    def schedule(self, vl_id: int, sequence: int, requested_ms: int, payload_bytes: int) -> AfdxDecision:
        link = self.links.get(vl_id)
        frame = AfdxFrame(vl_id, sequence, requested_ms, payload_bytes)
        if link is None:
            return AfdxDecision(False, "unknown_vl", frame)
        if payload_bytes > link.max_frame_bytes:
            return AfdxDecision(False, "frame_too_large", frame)
        if requested_ms < self.next_allowed[vl_id]:
            return AfdxDecision(False, "bag_violation", frame)
        self.next_allowed[vl_id] = requested_ms + link.bag_ms
        return AfdxDecision(True, "scheduled", frame)

    def transmit_redundant(self, decision: AfdxDecision) -> tuple[AfdxFrame, ...]:
        if not decision.accepted:
            return ()
        link = self.links[decision.frame.vl_id]
        if not link.redundant:
            return (decision.frame,)
        return (decision.frame, AfdxFrame(**{**asdict(decision.frame), "network": "B"}))

    def receive(self, frame: AfdxFrame) -> AfdxDecision:
        if frame.vl_id not in self.links:
            return AfdxDecision(False, "unknown_vl", frame)
        key = (frame.vl_id, frame.sequence)
        if key in self.seen:
            return AfdxDecision(False, "redundant_duplicate", frame)
        self.seen.add(key)
        return AfdxDecision(True, "delivered", frame)

    def reset(self) -> None:
        self.next_allowed = {link.vl_id: 0 for link in self.links.values()}
        self.seen.clear()


def run_scenarios() -> dict[str, object]:
    link = AfdxVirtualLink(1001, "VL-AIR-DATA", "ES-ADC", ("IOM-FCC", "IOM-DISPLAY"), bag_ms=4, max_frame_bytes=512, redundant=True)
    simulator = AfdxSimulator((link,))
    first = simulator.schedule(1001, 1, 0, 128)
    copies = simulator.transmit_redundant(first)
    deliveries = [simulator.receive(frame) for frame in copies]
    return {
        "model": {"name": "synthetic-afdx", "vl_count": 1, "networks": ["A", "B"]},
        "configuration": {"vl": asdict(link)},
        "normal": {"scheduled": first.accepted, "copies": len(copies), "deliveries": [asdict(item) for item in deliveries]},
        "attacks": {
            "bag_violation": {"decision": asdict(simulator.schedule(1001, 2, 0, 128))},
            "oversize": {"decision": asdict(simulator.schedule(1001, 3, 4, 513))},
            "unknown_vl": {"decision": asdict(simulator.schedule(9999, 1, 0, 128))},
            "redundant_duplicate": _duplicate_case(link),
        },
        "limitations": [
            "AFDX frames, VL IDs, BAG and endpoints are synthetic project data.",
            "The model does not open Ethernet sockets or validate physical AFDX waveforms.",
            "Configuration and implementation conclusions require an approved ICD, ES/switch versions and authorized bench evidence.",
        ],
    }


def _duplicate_case(link: AfdxVirtualLink) -> dict[str, object]:
    simulator = AfdxSimulator((link,))
    decision = simulator.schedule(link.vl_id, 1, 0, 128)
    copies = simulator.transmit_redundant(decision)
    results = [asdict(simulator.receive(frame)) for frame in copies]
    return {"copies": len(copies), "results": results}
