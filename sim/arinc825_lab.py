"""Deterministic, simulation-only ARINC 825/CAN bus model."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Iterable

class Arinc825Error(ValueError): pass

@dataclass(frozen=True)
class CanNode:
    node_id: str
    role: str
    authorized: bool = True
    error_passive_threshold: int = 3

@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    source: str
    sequence: int
    payload_bytes: int
    timestamp_ms: int
    extended: bool = False

@dataclass(frozen=True)
class CanDecision:
    accepted: bool
    reason: str
    frame: CanFrame

class Arinc825Bus:
    def __init__(self, nodes: Iterable[CanNode], *, bitrate: int = 500_000, window_ms: int = 1000) -> None:
        self.nodes = {node.node_id: node for node in nodes}
        if not self.nodes or bitrate <= 0 or window_ms <= 0:
            raise Arinc825Error("nodes, bitrate and window are required")
        self.bitrate, self.window_ms = bitrate, window_ms
        self.errors: dict[str, int] = {node.node_id: 0 for node in self.nodes.values()}
        self.seen: set[tuple[str, int, int]] = set()
        self.bits_in_window = 0

    def transmit(self, frame: CanFrame) -> CanDecision:
        node = self.nodes.get(frame.source)
        if node is None:
            return CanDecision(False, "unknown_node", frame)
        if not node.authorized:
            return CanDecision(False, "unauthorized_node", frame)
        if frame.payload_bytes < 0 or frame.payload_bytes > 8:
            return CanDecision(False, "payload_too_large", frame)
        key = (frame.source, frame.arbitration_id, frame.sequence)
        if key in self.seen:
            return CanDecision(False, "replay", frame)
        self.seen.add(key)
        self.bits_in_window += 47 + frame.payload_bytes * 8
        return CanDecision(True, "queued", frame)

    def arbitrate(self, frames: Iterable[CanFrame]) -> CanDecision | None:
        candidates = [self.transmit(frame) for frame in frames]
        accepted = [item for item in candidates if item.accepted]
        if not accepted:
            return candidates[0] if candidates else None
        return min(accepted, key=lambda item: item.frame.arbitration_id)

    def inject_error(self, node_id: str, count: int = 1) -> str:
        if node_id not in self.nodes or count <= 0:
            raise Arinc825Error("unknown node or invalid error count")
        self.errors[node_id] += count
        return "error_passive" if self.errors[node_id] >= self.nodes[node_id].error_passive_threshold else "error_active"

    def load_percent(self) -> float:
        return round(self.bits_in_window / (self.bitrate * self.window_ms / 1000) * 100, 3)


def run_scenarios() -> dict[str, object]:
    bus = Arinc825Bus((CanNode("POWER-LRU", "power"), CanNode("CABIN-LRU", "cabin"), CanNode("UNKNOWN", "test", authorized=False)))
    normal = bus.transmit(CanFrame(0x100, "POWER-LRU", 1, 4, 0))
    arbitration = bus.arbitrate((CanFrame(0x300, "CABIN-LRU", 1, 2, 1), CanFrame(0x200, "POWER-LRU", 2, 2, 1)))
    return {
        "model": {"name": "synthetic-arinc825-can", "bitrate": bus.bitrate, "payload_limit_bytes": 8},
        "normal": {"decision": asdict(normal), "load_percent": bus.load_percent()},
        "attacks": {
            "unauthorized_node": asdict(bus.transmit(CanFrame(0x100, "UNKNOWN", 1, 1, 2))),
            "replay": asdict(bus.transmit(CanFrame(0x100, "POWER-LRU", 1, 4, 3))),
            "arbitration": asdict(arbitration),
            "error_injection": {"state": bus.inject_error("CABIN-LRU", 3), "errors": bus.errors["CABIN-LRU"]},
            "oversize": asdict(bus.transmit(CanFrame(0x120, "POWER-LRU", 3, 9, 4))),
        },
        "limitations": ["CAN frames, node IDs and business semantics are synthetic.", "No CAN socket, transceiver, electrical waveform or aircraft equipment is opened.", "Real ARINC 825 behavior requires approved ICD, node configuration and authorized bench evidence."],
    }
