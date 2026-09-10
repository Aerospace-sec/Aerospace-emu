"""Extensible, simulation-only device twin primitives."""
from .model import (
    ConnectionSpec, DesiredState, DeviceAdapter, DeviceRegistry, DeviceSnapshot,
    DeviceSpec, InterfaceSpec, ObservedState, Reconciliation, StateEvent,
    TwinValidationError,
)

__all__ = [
    "ConnectionSpec", "DesiredState", "DeviceAdapter", "DeviceRegistry",
    "DeviceSnapshot", "DeviceSpec", "InterfaceSpec", "ObservedState",
    "Reconciliation", "StateEvent", "TwinValidationError",
]
