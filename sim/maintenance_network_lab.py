"""Synthetic maintenance Ethernet and data-loading security lab.

Models Ethernet-domain boundaries, FTP/ARINC 615A-style loading, UDP freshness,
and SNMP management authorization without opening sockets or loading a device.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Iterable

class MaintenanceNetworkError(ValueError): pass

@dataclass(frozen=True)
class NetworkZone:
    name: str
    vlan: int
    trust: str

@dataclass(frozen=True)
class MaintenanceSession:
    session_id: str
    source_zone: str
    role: str
    authenticated: bool
    maintenance_mode: bool
    dual_approval: bool = False

@dataclass(frozen=True)
class LoadPackage:
    name: str
    version: str
    digest: str
    signed: bool
    target_device: str

@dataclass(frozen=True)
class NetworkDecision:
    accepted: bool
    reason: str
    action: str

class MaintenanceNetwork:
    def __init__(self, zones: Iterable[NetworkZone]) -> None:
        self.zones = {zone.name: zone for zone in zones}
        if not self.zones:
            raise MaintenanceNetworkError("at least one network zone is required")
        self.last_udp_sequence: dict[str, int] = {}

    def boundary(self, session: MaintenanceSession, target_zone: str) -> NetworkDecision:
        if session.source_zone not in self.zones or target_zone not in self.zones:
            return NetworkDecision(False, "unknown_zone", "blocked")
        if not session.authenticated:
            return NetworkDecision(False, "authentication", "blocked")
        if target_zone == "AVIONICS" and (session.role != "maintenance" or not session.maintenance_mode or not session.dual_approval):
            return NetworkDecision(False, "cross_domain_policy", "blocked")
        return NetworkDecision(True, "zone_policy_pass", "route_allowed")

    def ftp_load(self, session: MaintenanceSession, package: LoadPackage) -> NetworkDecision:
        boundary = self.boundary(session, "AVIONICS")
        if not boundary.accepted:
            return boundary
        if not package.signed:
            return NetworkDecision(False, "package_signature", "rollback")
        if not package.digest or not package.version:
            return NetworkDecision(False, "package_metadata", "rollback")
        return NetworkDecision(True, "arinc615a_package_accepted", "staged_not_activated")

    def udp_observe(self, source_zone: str, stream: str, sequence: int, observed_ms: int, now_ms: int, freshness_ms: int = 1000) -> NetworkDecision:
        if source_zone not in self.zones:
            return NetworkDecision(False, "unknown_zone", "drop")
        if now_ms - observed_ms > freshness_ms:
            return NetworkDecision(False, "stale", "drop")
        previous = self.last_udp_sequence.get(stream, -1)
        if sequence <= previous:
            return NetworkDecision(False, "replay", "drop")
        self.last_udp_sequence[stream] = sequence
        return NetworkDecision(True, "fresh_sequence", "deliver_semantic_data")

    def snmp_request(self, session: MaintenanceSession, version: str, write: bool) -> NetworkDecision:
        if version not in {"v3-auth-priv", "v3-auth-noPriv"}:
            return NetworkDecision(False, "management_authentication", "blocked")
        if write and (session.role != "maintenance" or not session.dual_approval or not session.maintenance_mode):
            return NetworkDecision(False, "management_write_authorization", "blocked")
        return NetworkDecision(True, "management_policy_pass", "write_config" if write else "read_only")


def run_scenarios() -> dict[str, object]:
    network = MaintenanceNetwork((NetworkZone("CABIN", 10, "low"), NetworkZone("MAINTENANCE", 20, "controlled"), NetworkZone("AVIONICS", 30, "safety"), NetworkZone("GROUND", 40, "controlled")))
    authorized = MaintenanceSession("maint-1", "MAINTENANCE", "maintenance", True, True, True)
    ordinary = MaintenanceSession("cabin-1", "CABIN", "passenger", True, False, False)
    package = LoadPackage("synthetic-lru-image", "1.2.3", "sha256:synthetic", True, "SYNTHETIC_AIR_DATA_LRU")
    return {
        "model": {"name": "synthetic-maintenance-network", "protocols": ["ethernet", "ftp", "udp", "snmp", "arinc615a"], "simulation_only": True},
        "normal": {"boundary": asdict(network.boundary(authorized, "AVIONICS")), "load": asdict(network.ftp_load(authorized, package)), "udp": asdict(network.udp_observe("GROUND", "status", 1, 0, 100)), "snmp_read": asdict(network.snmp_request(authorized, "v3-auth-priv", False))},
        "attacks": {"cabin_boundary": asdict(network.boundary(ordinary, "AVIONICS")), "unsigned_load": asdict(network.ftp_load(authorized, LoadPackage(package.name, package.version, package.digest, False, package.target_device))), "udp_replay": asdict(network.udp_observe("GROUND", "status", 1, 0, 100)), "snmp_unauthorized_write": asdict(network.snmp_request(ordinary, "v3-auth-priv", True)), "snmp_legacy_version": asdict(network.snmp_request(authorized, "v2c", False))},
        "limitations": ["Zones, VLANs, roles, package metadata and protocol behavior are synthetic.", "No Ethernet socket, FTP server, SNMP agent or ARINC 615A loader is opened.", "Real data loading requires approved package signing, ICD, aircraft configuration and authorized bench evidence."],
    }
