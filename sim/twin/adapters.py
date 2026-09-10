"""Adapters that expose existing deterministic labs as twin observations."""
from __future__ import annotations
from typing import Any, Callable, Mapping
from .model import DeviceRegistry, ObservedState, StateEvent
from ..atg5g_lab import run_atg_scenarios
from ..serial_lab import run_serial_scenarios
from ..arinc429_lab import run_scenarios
from ..virtual_hardware_lab import run_virtual_hardware_lab


def _state(registry: DeviceRegistry, device_id: str, values: Mapping[str, Any], now_ms: int, sequence: int) -> None:
    registry.upsert_observed_state(device_id, ObservedState(dict(values), now_ms, "virtual_model", 1.0, sequence))


def _event(registry: DeviceRegistry, device_id: str, event_type: str, payload: Mapping[str, Any], now_ms: int, sequence: int) -> None:
    registry.append_event(StateEvent(f"{device_id}:{sequence}", event_type, now_ms, device_id, dict(payload), "virtual_model", sequence))


def populate_virtual_registry(registry: DeviceRegistry, *, scenario: str = "none", now_ms: int = 0) -> DeviceRegistry:
    """Run all local labs once and map their aggregate results into the registry."""
    sequence = 1
    try:
        atg = run_atg_scenarios()
        normal = atg.get("normal", {})
        for device_id, values in {
            "ATG-UE-001": {"registered": True, "user_plane_ready": True},
            "ATG-GNB-01": {"trusted": True, "cell_visible": True},
            "ATG-CORE-01": {"session_ready": True, "n6_ready": True},
            "N6-GROUND-APP": {"reachable": True},
        }.items():
            _state(registry, device_id, {**values, "scenario": "normal", "result_keys": list(normal)}, now_ms, sequence)
            sequence += 1
        _event(registry, "ATG-CORE-01", "scenario_complete", {"scenario": "normal", "result_keys": list(atg)}, now_ms, sequence); sequence += 1
    except Exception as exc:
        _event(registry, "ATG-CORE-01", "adapter_error", {"error": str(exc)}, now_ms, sequence); sequence += 1
    try:
        serial = run_serial_scenarios()
        for device_id, values in {"RS422-AVIONICS-LRU": {"link": "up", "protocol": "rs-422"}, "RS232-GSE-MAINT": {"link": "up", "protocol": "rs-232"}}.items():
            _state(registry, device_id, {**values, "result_keys": list(serial)}, now_ms, sequence); sequence += 1
    except Exception as exc:
        _event(registry, "RS422-AVIONICS-LRU", "adapter_error", {"error": str(exc)}, now_ms, sequence); sequence += 1
    try:
        arinc = run_scenarios()
        _state(registry, "V429-LAB-2T4R", {"bus": "up", "bit_rates": [12500, 100000], "result_keys": list(arinc)}, now_ms, sequence); sequence += 1
        _state(registry, "SYNTHETIC_AIR_DATA_LRU", {"receiver": "ready", "result_keys": list(arinc)}, now_ms, sequence); sequence += 1
    except Exception as exc:
        _event(registry, "V429-LAB-2T4R", "adapter_error", {"error": str(exc)}, now_ms, sequence); sequence += 1
    try:
        hardware = run_virtual_hardware_lab(duration_ms=200, step_ms=20, attack=scenario)
        health = hardware.get("health", {})
        _state(registry, "PX4-FMU-VIRTUAL", {"armed": False, "failsafe": health.get("failsafe", False), "scenario": scenario}, now_ms, sequence); sequence += 1
        _event(registry, "PX4-FMU-VIRTUAL", "scenario_complete", {"scenario": scenario, "simulation_only": True}, now_ms, sequence)
    except Exception as exc:
        _event(registry, "PX4-FMU-VIRTUAL", "adapter_error", {"error": str(exc)}, now_ms, sequence)
    return registry


class VirtualDeviceAdapter:
    def __init__(self, registry: DeviceRegistry, scenario: str = "none") -> None:
        self.registry, self.scenario = registry, scenario
    def discover(self): return tuple(self.registry.devices.values())
    def read_state(self, now_ms: int): return dict(self.registry.observed)
    def read_config(self): return {d.device_id: {"simulation_only": d.simulation_only} for d in self.discover()}
    def health_check(self, now_ms: int): return {d.device_id: self.registry.snapshot(d.device_id, now_ms).freshness for d in self.discover()}
    def capabilities(self): return ("read_state", "read_topology")
    def close(self): return None
