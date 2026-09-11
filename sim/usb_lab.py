"""Deterministic, simulation-only aviation USB boundary model."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Iterable

class UsbLabError(ValueError): pass

@dataclass(frozen=True)
class UsbPort:
    port_id: str
    zone: str
    role: str
    power_w: float = 7.5

@dataclass(frozen=True)
class UsbDevice:
    vendor_id: str
    product_id: str
    serial: str
    device_class: str
    firmware_version: str
    firmware_signed: bool
    storage_digest: str

@dataclass(frozen=True)
class UsbDecision:
    accepted: bool
    reason: str
    action: str

class UsbBoundary:
    def __init__(self, ports: Iterable[UsbPort], *, approved_devices: Iterable[str] = ()) -> None:
        self.ports = {port.port_id: port for port in ports}
        self.approved_devices = set(approved_devices)
        if not self.ports:
            raise UsbLabError("at least one USB port is required")

    def enumerate(self, port_id: str, device: UsbDevice) -> UsbDecision:
        port = self.ports.get(port_id)
        if port is None:
            return UsbDecision(False, "unknown_port", "isolate")
        if device.serial not in self.approved_devices:
            return UsbDecision(False, "device_not_approved", "isolate")
        if not device.firmware_signed:
            return UsbDecision(False, "firmware_signature", "isolate")
        if device.device_class not in {"mass_storage", "diagnostic"}:
            return UsbDecision(False, "device_class_policy", "isolate")
        if port.zone == "CABIN":
            return UsbDecision(True, "cabin_data_disabled", "charge_only")
        return UsbDecision(True, "enumeration_allowed", "read_only")

    def data_transfer(self, port_id: str, device: UsbDevice, bytes_count: int) -> UsbDecision:
        port = self.ports.get(port_id)
        if port is None:
            return UsbDecision(False, "unknown_port", "isolate")
        decision = self.enumerate(port_id, device)
        if not decision.accepted or decision.action == "charge_only":
            return decision
        if bytes_count < 0 or bytes_count > 16 * 1024 * 1024:
            return UsbDecision(False, "transfer_limit", "drop")
        return UsbDecision(True, "transfer_allowed", "audit_and_scan")


def run_scenarios() -> dict[str, object]:
    maintenance = UsbPort("USB-MAINT-1", "MAINTENANCE", "maintenance_data")
    cabin = UsbPort("USB-CABIN-1", "CABIN", "charging_only")
    approved = UsbDevice("SYN", "LAPTOP", "approved-001", "mass_storage", "1.0", True, "sha256:synthetic")
    boundary = UsbBoundary((maintenance, cabin), approved_devices=(approved.serial,))
    return {
        "model": {"name": "synthetic-aviation-usb", "zones": ["MAINTENANCE", "CABIN"], "simulation_only": True},
        "normal": {"maintenance_enumeration": asdict(boundary.enumerate(maintenance.port_id, approved)), "maintenance_transfer": asdict(boundary.data_transfer(maintenance.port_id, approved, 4096))},
        "attacks": {
            "unknown_device": asdict(boundary.enumerate(maintenance.port_id, UsbDevice("SYN", "BAD", "unknown", "mass_storage", "1.0", True, "sha256:x"))),
            "unsigned_firmware": asdict(boundary.enumerate(maintenance.port_id, UsbDevice("SYN", "BAD", approved.serial, "mass_storage", "1.1", False, "sha256:x"))),
            "cabin_data": asdict(boundary.enumerate(cabin.port_id, approved)),
            "class_spoof": asdict(boundary.enumerate(maintenance.port_id, UsbDevice("SYN", "HID", approved.serial, "hid", "1.0", True, "sha256:x"))),
            "oversize_transfer": asdict(boundary.data_transfer(maintenance.port_id, approved, 16 * 1024 * 1024 + 1)),
        },
        "limitations": ["USB identifiers, classes, ports and firmware metadata are synthetic.", "No USB host controller, HID injection, storage device or aircraft computer is opened.", "Real maintenance USB validation requires approved device-control policy, signed firmware and authorized bench evidence."],
    }
