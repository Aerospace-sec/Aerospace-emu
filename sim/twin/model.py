"""Core identity, topology, state, and event types for the device twin."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol


class TwinValidationError(ValueError):
    pass


@dataclass(frozen=True)
class InterfaceSpec:
    interface_id: str
    kind: str
    direction: str = "bidirectional"
    protocol_profile: str = "synthetic"
    enabled: bool = True


@dataclass(frozen=True)
class DeviceSpec:
    device_id: str
    name: str
    device_class: str
    domain: str
    model_ref: str = ""
    visual_ref: str = "device"
    simulation_only: bool = True
    interfaces: tuple[InterfaceSpec, ...] = ()
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionSpec:
    connection_id: str
    source_device: str
    source_interface: str
    target_device: str
    target_interface: str
    protocol: str
    direction: str = "bidirectional"


@dataclass(frozen=True)
class ObservedState:
    values: Mapping[str, Any]
    observed_at: int
    source: str
    confidence: float = 1.0
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.observed_at < 0 or not 0 <= self.confidence <= 1:
            raise TwinValidationError("invalid observation timestamp or confidence")
        if not self.source:
            raise TwinValidationError("observation source is required")


@dataclass(frozen=True)
class DesiredState:
    values: Mapping[str, Any]
    revision: int = 0


@dataclass(frozen=True)
class Reconciliation:
    status: str
    differences: Mapping[str, tuple[Any, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceSnapshot:
    device: DeviceSpec
    observed: ObservedState | None
    desired: DesiredState | None
    reconciliation: Reconciliation
    freshness: str
    health: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateEvent:
    event_id: str
    event_type: str
    timestamp_ms: int
    device_id: str
    payload: Mapping[str, Any]
    source: str
    sequence: int = 0
    interface_id: str | None = None


class DeviceAdapter(Protocol):
    def discover(self) -> tuple[DeviceSpec, ...]: ...
    def read_state(self, now_ms: int) -> Mapping[str, ObservedState]: ...
    def read_config(self) -> Mapping[str, Any]: ...
    def health_check(self, now_ms: int) -> Mapping[str, Any]: ...
    def capabilities(self) -> tuple[str, ...]: ...
    def close(self) -> None: ...


class DeviceRegistry:
    def __init__(self, *, stale_after_ms: int = 5_000) -> None:
        if stale_after_ms <= 0:
            raise TwinValidationError("stale_after_ms must be positive")
        self.stale_after_ms = stale_after_ms
        self.devices: dict[str, DeviceSpec] = {}
        self.connections: dict[str, ConnectionSpec] = {}
        self.observed: dict[str, ObservedState] = {}
        self.desired: dict[str, DesiredState] = {}
        self.events: list[StateEvent] = []

    def register_device(self, device: DeviceSpec) -> None:
        if not device.device_id or device.device_id in self.devices:
            raise TwinValidationError(f"duplicate or empty device_id: {device.device_id!r}")
        interface_ids = [item.interface_id for item in device.interfaces]
        if not all(interface_ids) or len(interface_ids) != len(set(interface_ids)):
            raise TwinValidationError(f"duplicate or empty interface on {device.device_id}")
        self.devices[device.device_id] = device

    def register_connection(self, connection: ConnectionSpec) -> None:
        if connection.connection_id in self.connections:
            raise TwinValidationError(f"duplicate connection_id: {connection.connection_id}")
        for device_id, interface_id in ((connection.source_device, connection.source_interface), (connection.target_device, connection.target_interface)):
            device = self.devices.get(device_id)
            if device is None or interface_id not in {item.interface_id for item in device.interfaces}:
                raise TwinValidationError(f"unknown connection endpoint: {device_id}/{interface_id}")
        self.connections[connection.connection_id] = connection

    def get_device(self, device_id: str) -> DeviceSpec:
        try:
            return self.devices[device_id]
        except KeyError as exc:
            raise TwinValidationError(f"unknown device: {device_id}") from exc

    def upsert_observed_state(self, device_id: str, state: ObservedState) -> None:
        self.get_device(device_id)
        old = self.observed.get(device_id)
        if old is not None and state.sequence < old.sequence:
            return
        self.observed[device_id] = state

    def set_desired_state(self, device_id: str, state: DesiredState) -> None:
        self.get_device(device_id)
        self.desired[device_id] = state

    def append_event(self, event: StateEvent) -> None:
        self.get_device(event.device_id)
        if self.events and event.sequence < self.events[-1].sequence:
            raise TwinValidationError("event sequence must be monotonic")
        self.events.append(event)

    def snapshot(self, device_id: str, now_ms: int) -> DeviceSnapshot:
        device = self.get_device(device_id)
        observed = self.observed.get(device_id)
        desired = self.desired.get(device_id)
        if observed is None:
            freshness = "unknown"
        elif now_ms - observed.observed_at > self.stale_after_ms:
            freshness = "stale"
        else:
            freshness = "fresh"
        differences: dict[str, tuple[Any, Any]] = {}
        if desired and observed:
            for key, wanted in desired.values.items():
                if observed.values.get(key) != wanted:
                    differences[key] = (observed.values.get(key), wanted)
        reconciliation = Reconciliation("drifted" if differences else "in_sync", differences)
        return DeviceSnapshot(device, observed, desired, reconciliation, freshness)

    def snapshots(self, now_ms: int) -> tuple[DeviceSnapshot, ...]:
        return tuple(self.snapshot(device_id, now_ms) for device_id in self.devices)


def to_json(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_json(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json(item) for item in value]
    return value
