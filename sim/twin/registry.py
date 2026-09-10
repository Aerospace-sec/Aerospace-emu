"""Load validated device/topology descriptions from JSON."""
from __future__ import annotations
import json
from pathlib import Path
from .model import ConnectionSpec, DeviceRegistry, DeviceSpec, InterfaceSpec, TwinValidationError


def load_registry(path: str | Path) -> DeviceRegistry:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TwinValidationError(f"cannot load registry {source}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("devices"), list) or not isinstance(document.get("connections"), list):
        raise TwinValidationError("registry requires devices and connections arrays")
    registry = DeviceRegistry(stale_after_ms=int(document.get("stale_after_ms", 5000)))
    try:
        for raw in document["devices"]:
            interfaces = tuple(InterfaceSpec(**item) for item in raw.pop("interfaces", []))
            registry.register_device(DeviceSpec(interfaces=interfaces, capabilities=tuple(raw.pop("capabilities", [])), **raw))
        for raw in document["connections"]:
            registry.register_connection(ConnectionSpec(**raw))
    except (KeyError, TypeError, ValueError) as exc:
        raise TwinValidationError(f"invalid registry entry: {exc}") from exc
    return registry
