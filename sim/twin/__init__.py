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
    "CausalEvent", "EvidenceRecord", "SimulationClock", "run_cross_model_chain",
]

from .chain import CausalEvent, EvidenceRecord, SimulationClock, run_cross_model_chain
from .message import MessageEnvelope, MessageValidationError, PolicyDecision
