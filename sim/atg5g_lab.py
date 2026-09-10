"""Digital 5G-ATG security laboratory for civil-aviation scenarios.

The model represents a 5G air-to-ground service as a 3GPP 5GS profile:

    aircraft 5G UE -> NR/ATG gNB -> AMF/SMF/UPF -> N6 ground application

It also models a cabin-to-avionics boundary and a maintenance/OAM boundary.
The simulation is deliberately digital and local. It does not transmit RF,
create a cell, operate a gNB/5GC, open a socket, or connect to an aircraft.

The message fields, UE identifiers, PLMN, DNN, slice, TEID and business
commands are synthetic project ICD data. 3GPP procedures are represented as
state transitions so that attack preconditions and security decisions can be
tested without providing an operational RF attack tool.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
from typing import Any, Iterable, Mapping


class Atg5gLabError(ValueError):
    """Raised for invalid 5G-ATG laboratory configuration or input."""


SYNTHETIC_PLMN = "999-99"
SYNTHETIC_SLICE = "sst=1;sd=ATG001"
SYNTHETIC_DNNS = ("atg.cabin", "atg.maint", "atg.avionics")
MAX_PAYLOAD_BYTES = 4096


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise Atg5gLabError("payload must be JSON-compatible") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise Atg5gLabError("payload exceeds synthetic data-plane limit")
    return encoded


@dataclass(frozen=True)
class AtgCell:
    """Synthetic ATG cell broadcast profile."""

    cell_id: str
    plmn: str = SYNTHETIC_PLMN
    tac: str = "ATG-TAC-01"
    arfcn: int = 640000
    trusted_gnb: bool = True
    atg_capable: bool = True
    altitude_band_ft: tuple[int, int] = (0, 45_000)


@dataclass(frozen=True)
class RegistrationResult:
    """Digital RRC/NAS registration state observed by the UE."""

    ue_id: str
    cell_id: str | None
    broadcast_visible: bool
    pre_security_metadata_visible: bool
    rrc_setup_complete: bool
    nas_registration_request_sent: bool
    aka_success: bool
    security_mode_complete: bool
    camped_on_cell: bool
    user_plane_ready: bool
    result: str
    reason: str


class AtgAirInterface:
    """Model NR/ATG cell selection and pre-/post-security states."""

    def __init__(self, cells: Iterable[AtgCell]) -> None:
        self.cells = tuple(cells)
        if not self.cells:
            raise Atg5gLabError("at least one ATG cell is required")
        if len({cell.cell_id for cell in self.cells}) != len(self.cells):
            raise Atg5gLabError("cell IDs must be unique")
        self.events: list[dict[str, Any]] = []

    def visible_cells(self, *, plmn: str = SYNTHETIC_PLMN) -> list[AtgCell]:
        return [cell for cell in self.cells if cell.plmn == plmn and cell.atg_capable]

    def register(
        self,
        *,
        ue_id: str,
        preferred_cell_id: str | None = None,
        interference: bool = False,
        ue_validates_gnb: bool = True,
        now_ms: int = 0,
    ) -> RegistrationResult:
        visible = self.visible_cells()
        if interference:
            result = RegistrationResult(
                ue_id,
                None,
                bool(visible),
                bool(visible),
                False,
                False,
                False,
                False,
                False,
                False,
                "registration_timeout",
                "uu_interference_or_signal_blocking",
            )
            self.events.append({"timestamp_ms": now_ms, "event": "RRC_NAS_TIMEOUT", "ue_id": ue_id})
            return result

        selected = None
        if preferred_cell_id is not None:
            selected = next((cell for cell in visible if cell.cell_id == preferred_cell_id), None)
        if selected is None and visible:
            selected = visible[0]
        if selected is None:
            result = RegistrationResult(
                ue_id, None, False, False, False, False, False, False, False, False,
                "no_cell", "no_atg_cell_visible",
            )
            self.events.append({"timestamp_ms": now_ms, "event": "NO_CELL", "ue_id": ue_id})
            return result

        # SIB/broadcast and the initial RRC/NAS exchange are observable before
        # the security context is complete. A rogue cell can therefore cause
        # camping/metadata exposure or denial even though 5G-AKA later fails.
        pre_security_visible = True
        if not selected.trusted_gnb:
            camped = not ue_validates_gnb
            result = RegistrationResult(
                ue_id,
                selected.cell_id,
                True,
                pre_security_visible,
                True,
                True,
                False,
                False,
                camped,
                False,
                "rogue_cell_rejected" if ue_validates_gnb else "camped_untrusted_cell",
                "5g_aka_or_network_trust_failed",
            )
            self.events.append(
                {
                    "timestamp_ms": now_ms,
                    "event": "UNTRUSTED_GNB_SELECTED",
                    "ue_id": ue_id,
                    "cell_id": selected.cell_id,
                    "registration_request_visible": True,
                }
            )
            return result

        result = RegistrationResult(
            ue_id,
            selected.cell_id,
            True,
            pre_security_visible,
            True,
            True,
            True,
            True,
            True,
            True,
            "registered",
            "5g_aka_and_security_mode_complete",
        )
        self.events.append(
            {
                "timestamp_ms": now_ms,
                "event": "REGISTRATION_COMPLETE",
                "ue_id": ue_id,
                "cell_id": selected.cell_id,
                "security_mode_complete": True,
            }
        )
        return result


@dataclass(frozen=True)
class AtgSession:
    ue_id: str
    dnn: str
    slice_id: str
    teid: int
    source_zone: str
    cell_id: str
    security_established: bool = True
    qfi: int = 9


class AtgCore:
    """Synthetic AMF/SMF/UPF session state and registration pressure model."""

    def __init__(self, *, authorized_ues: Iterable[str], max_signaling_per_window: int = 10) -> None:
        self.authorized_ues = frozenset(authorized_ues)
        self.max_signaling_per_window = max_signaling_per_window
        self.sessions: dict[tuple[str, str], AtgSession] = {}
        self.signaling_times: deque[int] = deque()
        self.registration_events: list[dict[str, Any]] = []
        self.next_teid = 0x1000

    def establish_session(
        self,
        *,
        ue_id: str,
        cell_id: str,
        dnn: str,
        slice_id: str,
        source_zone: str,
        now_ms: int,
        qfi: int = 9,
    ) -> AtgSession:
        if ue_id not in self.authorized_ues:
            raise Atg5gLabError("ue_not_authorized")
        if dnn not in SYNTHETIC_DNNS:
            raise Atg5gLabError("dnn_not_provisioned")
        if not 1 <= qfi <= 63:
            raise Atg5gLabError("qfi_out_of_range")
        session = AtgSession(ue_id, dnn, slice_id, self.next_teid, source_zone, cell_id, True, qfi)
        self.next_teid += 1
        self.sessions[(ue_id, dnn)] = session
        self.registration_events.append(
            {
                "timestamp_ms": now_ms,
                "event": "PDU_SESSION_ESTABLISHMENT_ACCEPT",
                "ue_id": ue_id,
                "dnn": dnn,
                "teid": session.teid,
            }
        )
        return session

    def signaling_attempt(self, *, source: str, now_ms: int, hardened: bool) -> dict[str, Any]:
        cutoff = now_ms - 1000
        while self.signaling_times and self.signaling_times[0] <= cutoff:
            self.signaling_times.popleft()
        if hardened and len(self.signaling_times) >= self.max_signaling_per_window:
            result = {
                "accepted": False,
                "reason": "signaling_rate_limit",
                "source": source,
            }
        else:
            self.signaling_times.append(now_ms)
            result = {
                "accepted": True,
                "reason": "registration_context_allocated",
                "source": source,
            }
        self.registration_events.append({"timestamp_ms": now_ms, "event": "REGISTRATION_ATTEMPT", **result})
        return result


@dataclass(frozen=True)
class AtgUserPacket:
    """Synthetic N3/N6 data-plane packet after UE user-plane setup."""

    timestamp_ms: int
    source_id: str
    source_zone: str
    destination_zone: str
    dnn: str
    slice_id: str
    qfi: int
    teid: int
    message_type: str
    payload: dict[str, Any]
    sequence: int
    app_tls: bool = False
    tag: str | None = None


def _packet_auth_input(packet: AtgUserPacket) -> bytes:
    data = {
        "timestamp_ms": packet.timestamp_ms,
        "source_id": packet.source_id,
        "source_zone": packet.source_zone,
        "destination_zone": packet.destination_zone,
        "dnn": packet.dnn,
        "slice_id": packet.slice_id,
        "qfi": packet.qfi,
        "teid": packet.teid,
        "message_type": packet.message_type,
        "payload": packet.payload,
        "sequence": packet.sequence,
        "app_tls": packet.app_tls,
    }
    return _canonical_json(data)


def make_authenticated_packet(packet: AtgUserPacket, secret: bytes) -> AtgUserPacket:
    if not secret:
        raise Atg5gLabError("packet secret must not be empty")
    tag = hmac.new(secret, _packet_auth_input(packet), hashlib.sha256).hexdigest()
    return replace(packet, tag=tag)


@dataclass(frozen=True)
class AtgAdmission:
    accepted: bool
    reason: str
    effect: str
    packet: AtgUserPacket


class GroundApplication:
    """Synthetic ground/cabin/avionics consumers for business-impact proof."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "ground_telemetry": None,
            "maintenance_records": [],
            "avionics_commands": [],
            "cabin_messages": [],
        }

    def consume(self, packet: AtgUserPacket) -> str:
        if packet.destination_zone == "AVIONICS":
            command = packet.payload.get("command", packet.message_type)
            self.state["avionics_commands"].append(command)
            return f"avionics_command_executed:{command}"
        if packet.message_type == "AIRCRAFT_TELEMETRY":
            self.state["ground_telemetry"] = dict(packet.payload)
            return f"telemetry_updated:{packet.payload.get('altitude_ft')}ft"
        if packet.message_type == "MAINTENANCE_RECORD":
            self.state["maintenance_records"].append(dict(packet.payload))
            return "maintenance_record_stored"
        self.state["cabin_messages"].append(dict(packet.payload))
        return "cabin_data_forwarded"


class NativeAtgRouter:
    """Legacy trusted-transport model with weak inter-zone policy."""

    def __init__(self, app: GroundApplication) -> None:
        self.app = app
        self.accepted: list[AtgPacket] = []

    def forward(self, packet: AtgUserPacket, *, now_ms: int) -> AtgAdmission:
        if packet.dnn not in SYNTHETIC_DNNS:
            return AtgAdmission(False, "unknown_dnn", "no_application_delivery", packet)
        # Deliberately weak: a trusted DNN/TEID is treated as sufficient and
        # source-zone to destination-zone policy is not enforced.
        effect = self.app.consume(packet)
        self.accepted.append(packet)
        return AtgAdmission(True, "accepted_trusted_transport", effect, packet)


class SecureAtgGateway:
    """Hardened N3/N6 and cabin/avionics boundary policy."""

    def __init__(
        self,
        *,
        app: GroundApplication,
        secrets: Mapping[str, bytes],
        sessions: Mapping[tuple[str, str], AtgSession],
        require_app_tls_for: Iterable[str] = ("AVIONICS", "GROUND_MAINT"),
        max_age_ms: int = 100,
        max_future_ms: int = 20,
        max_packets_per_window: int = 20,
    ) -> None:
        if not secrets or any(not key for key in secrets.values()):
            raise Atg5gLabError("gateway secrets must not be empty")
        self.app = app
        self.secrets = dict(secrets)
        self.sessions = dict(sessions)
        self.require_app_tls_for = frozenset(require_app_tls_for)
        self.max_age_ms = max_age_ms
        self.max_future_ms = max_future_ms
        self.max_packets_per_window = max_packets_per_window
        self.last_sequence: dict[tuple[str, str, str], int] = {}
        self.accept_times: deque[int] = deque()
        self.accepted: list[AtgPacket] = []

    def _rate_limited(self, now_ms: int) -> bool:
        cutoff = now_ms - 1000
        while self.accept_times and self.accept_times[0] <= cutoff:
            self.accept_times.popleft()
        return len(self.accept_times) >= self.max_packets_per_window

    def admit(self, packet: AtgUserPacket, *, now_ms: int) -> AtgAdmission:
        session = self.sessions.get((packet.source_id, packet.dnn))
        if session is None:
            return AtgAdmission(False, "no_active_pdu_session", "no_application_delivery", packet)
        if packet.teid != session.teid or packet.slice_id != session.slice_id:
            return AtgAdmission(False, "session_binding", "no_application_delivery", packet)
        if packet.qfi != session.qfi:
            return AtgAdmission(False, "qfi_binding", "no_application_delivery", packet)
        if packet.source_zone != session.source_zone:
            return AtgAdmission(False, "source_zone_binding", "no_application_delivery", packet)
        if packet.source_zone == "CABIN" and packet.destination_zone == "AVIONICS":
            return AtgAdmission(False, "cabin_to_avionics_boundary", "no_application_delivery", packet)
        if packet.destination_zone in self.require_app_tls_for and not packet.app_tls:
            return AtgAdmission(False, "application_integrity_required", "no_application_delivery", packet)
        secret = self.secrets.get(packet.source_id)
        if secret is None:
            return AtgAdmission(False, "unauthorized_source", "no_application_delivery", packet)
        expected = hmac.new(secret, _packet_auth_input(replace(packet, tag=None)), hashlib.sha256).hexdigest()
        if packet.tag is None or not hmac.compare_digest(packet.tag, expected):
            return AtgAdmission(False, "authentication", "no_application_delivery", packet)
        age = now_ms - packet.timestamp_ms
        if age > self.max_age_ms:
            return AtgAdmission(False, "stale", "no_application_delivery", packet)
        if age < -self.max_future_ms:
            return AtgAdmission(False, "future_timestamp", "no_application_delivery", packet)
        key = (packet.source_id, packet.dnn, packet.message_type)
        previous = self.last_sequence.get(key)
        if previous is not None and packet.sequence <= previous:
            return AtgAdmission(False, "replay", "no_application_delivery", packet)
        if not _validate_user_payload(packet):
            return AtgAdmission(False, "semantic_validation", "no_application_delivery", packet)
        if self._rate_limited(now_ms):
            return AtgAdmission(False, "user_plane_rate_limit", "no_application_delivery", packet)
        self.last_sequence[key] = packet.sequence
        self.accept_times.append(now_ms)
        effect = self.app.consume(packet)
        self.accepted.append(packet)
        return AtgAdmission(True, "accepted", effect, packet)


def _validate_user_payload(packet: AtgUserPacket) -> bool:
    if packet.message_type == "AIRCRAFT_TELEMETRY":
        return (
            isinstance(packet.payload.get("altitude_ft"), (int, float))
            and 0 <= float(packet.payload["altitude_ft"]) <= 50_000
            and isinstance(packet.payload.get("airspeed_kt"), (int, float))
            and 0 <= float(packet.payload["airspeed_kt"]) <= 600
        )
    if packet.message_type == "AVIONICS_COMMAND":
        return isinstance(packet.payload.get("command"), str) and bool(packet.payload["command"])
    if packet.message_type == "MAINTENANCE_RECORD":
        return isinstance(packet.payload.get("record_id"), str)
    return True


@dataclass(frozen=True)
class OamRequest:
    source: str
    role: str
    operation: str
    target: str
    config: dict[str, Any]
    timestamp_ms: int
    sequence: int
    mtls: bool = False
    dual_approval: bool = False
    tag: str | None = None


def _oam_auth_input(request: OamRequest) -> bytes:
    return _canonical_json(
        {
            "source": request.source,
            "role": request.role,
            "operation": request.operation,
            "target": request.target,
            "config": request.config,
            "timestamp_ms": request.timestamp_ms,
            "sequence": request.sequence,
            "mtls": request.mtls,
            "dual_approval": request.dual_approval,
        }
    )


def make_oam_request(request: OamRequest, secret: bytes) -> OamRequest:
    tag = hmac.new(secret, _oam_auth_input(request), hashlib.sha256).hexdigest()
    return replace(request, tag=tag)


class NativeOamController:
    """Legacy private-core OAM bridge that trusts a maintenance jump host."""

    def __init__(self) -> None:
        self.config = {"cabin_to_avionics_route": True, "slice_isolation": False}

    def apply(self, request: OamRequest) -> dict[str, Any]:
        if request.operation != "UPDATE_ROUTE_POLICY":
            return {"accepted": False, "reason": "unsupported_operation"}
        self.config.update(request.config)
        return {"accepted": True, "reason": "trusted_oam_bridge", "config": dict(self.config)}


class SecureOamController:
    """mTLS/RBAC/dual-approval OAM boundary for the dedicated core."""

    def __init__(self, *, secret: bytes) -> None:
        self.secret = secret
        self.config = {"cabin_to_avionics_route": False, "slice_isolation": True}
        self.last_sequence: dict[str, int] = {}

    def apply(self, request: OamRequest, *, now_ms: int) -> dict[str, Any]:
        if request.source != "network_admin" or request.role != "network_admin":
            return {"accepted": False, "reason": "oam_rbac"}
        if not request.mtls or not request.dual_approval:
            return {"accepted": False, "reason": "oam_approval"}
        expected = hmac.new(self.secret, _oam_auth_input(replace(request, tag=None)), hashlib.sha256).hexdigest()
        if request.tag is None or not hmac.compare_digest(request.tag, expected):
            return {"accepted": False, "reason": "oam_authentication"}
        if now_ms - request.timestamp_ms > 100:
            return {"accepted": False, "reason": "oam_stale"}
        if request.sequence <= self.last_sequence.get(request.source, -1):
            return {"accepted": False, "reason": "oam_replay"}
        if request.operation != "UPDATE_ROUTE_POLICY":
            return {"accepted": False, "reason": "unsupported_operation"}
        # A security controller does not allow a route that crosses the
        # cabin/avionics boundary, even when an operator asks for it.
        if request.config.get("cabin_to_avionics_route") is True:
            return {"accepted": False, "reason": "protected_boundary_policy"}
        self.last_sequence[request.source] = request.sequence
        self.config.update(request.config)
        return {"accepted": True, "reason": "oam_authorized", "config": dict(self.config)}


def _new_session(core: AtgCore, *, ue_id: str, dnn: str, zone: str = "CABIN") -> AtgSession:
    return core.establish_session(
        ue_id=ue_id,
        cell_id="ATG-GNB-01",
        dnn=dnn,
        slice_id=SYNTHETIC_SLICE,
        source_zone=zone,
        now_ms=0,
    )


def _packet(
    *,
    timestamp_ms: int,
    source_id: str,
    source_zone: str,
    destination_zone: str,
    dnn: str,
    session: AtgSession,
    message_type: str,
    payload: Mapping[str, Any],
    sequence: int,
    app_tls: bool,
    secret: bytes | None = None,
) -> AtgUserPacket:
    packet = AtgUserPacket(
        timestamp_ms,
        source_id,
        source_zone,
        destination_zone,
        dnn,
        session.slice_id,
        session.qfi,
        session.teid,
        message_type,
        dict(payload),
        sequence,
        app_tls,
        None,
    )
    return make_authenticated_packet(packet, secret) if secret is not None else packet


def _summary(admission: AtgAdmission) -> dict[str, Any]:
    return {
        "accepted": admission.accepted,
        "reason": admission.reason,
        "effect": admission.effect,
    }


def run_atg_scenarios() -> dict[str, Any]:
    """Run deterministic 5G-ATG full-chain scenarios."""

    trusted_cell = AtgCell("ATG-GNB-01")
    rogue_cell = AtgCell("ROGUE-GNB-01", trusted_gnb=False)
    air = AtgAirInterface((trusted_cell, rogue_cell))
    ue_id = "ATG-UE-001"
    registration = air.register(ue_id=ue_id, preferred_cell_id="ATG-GNB-01", now_ms=0)
    rogue_hardened = air.register(ue_id=ue_id, preferred_cell_id="ROGUE-GNB-01", ue_validates_gnb=True, now_ms=100)
    rogue_legacy = air.register(ue_id=ue_id, preferred_cell_id="ROGUE-GNB-01", ue_validates_gnb=False, now_ms=200)
    interference = air.register(ue_id=ue_id, preferred_cell_id="ATG-GNB-01", interference=True, now_ms=300)

    core = AtgCore(authorized_ues={ue_id, "ATG-AVIONICS-001", "ATG-MAINT-001"}, max_signaling_per_window=10)
    cabin_session = _new_session(core, ue_id=ue_id, dnn="atg.cabin", zone="CABIN")
    avionics_session = _new_session(core, ue_id="ATG-AVIONICS-001", dnn="atg.avionics", zone="AVIONICS")
    maint_session = _new_session(core, ue_id="ATG-MAINT-001", dnn="atg.maint", zone="GROUND_MAINT")

    app_native = GroundApplication()
    app_secure = GroundApplication()
    native_router = NativeAtgRouter(app_native)
    secure_gateway = SecureAtgGateway(
        app=app_secure,
        secrets={
            ue_id: b"cabin-user-key",
            "ATG-AVIONICS-001": b"avionics-key",
            "ATG-MAINT-001": b"maint-key",
            "network_admin": b"oam-key",
        },
        sessions={
            (ue_id, cabin_session.dnn): cabin_session,
            (avionics_session.ue_id, avionics_session.dnn): avionics_session,
            (maint_session.ue_id, maint_session.dnn): maint_session,
        },
    )

    normal_telemetry = _packet(
        timestamp_ms=0,
        source_id="ATG-AVIONICS-001",
        source_zone="AVIONICS",
        destination_zone="GROUND_MONITOR",
        dnn="atg.avionics",
        session=avionics_session,
        message_type="AIRCRAFT_TELEMETRY",
        payload={"altitude_ft": 30_000, "airspeed_kt": 440, "phase": "cruise"},
        sequence=1,
        app_tls=True,
        secret=b"avionics-key",
    )
    normal_native = native_router.forward(replace(normal_telemetry, tag=None, app_tls=False), now_ms=0)
    normal_hardened = secure_gateway.admit(normal_telemetry, now_ms=0)

    capture_air = {
        "broadcast_sib_visible": True,
        "pre_security_registration_metadata_visible": True,
        "user_plane_payload_readable_after_security": False,
        "reason": "5g_air_interface_security_context_not_yet_complete_for_broadcast_and_initial_signaling",
    }
    capture_n3 = {
        "gtp_u_outer_headers_visible": True,
        "teid_visible_to_transport_observer": True,
        "application_payload_readable_without_app_tls": True,
        "application_payload_readable_with_app_tls": False,
        "reason": "N3_transport_and_N6_application_protection_are_deployment_controls",
    }

    cabin_to_avionics = _packet(
        timestamp_ms=100,
        source_id=ue_id,
        source_zone="CABIN",
        destination_zone="AVIONICS",
        dnn="atg.cabin",
        session=cabin_session,
        message_type="AVIONICS_COMMAND",
        payload={"command": "SET_FLIGHT_DISPLAY_MODE", "mode": "TEST"},
        sequence=1,
        app_tls=False,
        secret=None,
    )
    cabin_native = native_router.forward(cabin_to_avionics, now_ms=100)
    cabin_hardened = secure_gateway.admit(cabin_to_avionics, now_ms=100)

    tamper_original = _packet(
        timestamp_ms=200,
        source_id="ATG-AVIONICS-001",
        source_zone="AVIONICS",
        destination_zone="GROUND_MONITOR",
        dnn="atg.avionics",
        session=avionics_session,
        message_type="AIRCRAFT_TELEMETRY",
        payload={"altitude_ft": 30_000, "airspeed_kt": 440, "phase": "cruise"},
        sequence=2,
        app_tls=True,
        secret=b"avionics-key",
    )
    tampered = replace(tamper_original, payload={"altitude_ft": 500, "airspeed_kt": 80, "phase": "cruise"})
    tamper_native = native_router.forward(replace(tampered, tag=None, app_tls=False), now_ms=200)
    tamper_hardened = secure_gateway.admit(tampered, now_ms=200)

    replay_packet = _packet(
        timestamp_ms=300,
        source_id="ATG-AVIONICS-001",
        source_zone="AVIONICS",
        destination_zone="GROUND_MONITOR",
        dnn="atg.avionics",
        session=avionics_session,
        message_type="AIRCRAFT_TELEMETRY",
        payload={"altitude_ft": 30_000, "airspeed_kt": 440, "phase": "cruise"},
        sequence=3,
        app_tls=True,
        secret=b"avionics-key",
    )
    replay_native = native_router.forward(replace(replay_packet, tag=None, app_tls=False), now_ms=300)
    replay_first = secure_gateway.admit(replay_packet, now_ms=300)
    replay_again = secure_gateway.admit(replay_packet, now_ms=320)

    session_forged = replace(
        cabin_to_avionics,
        destination_zone="GROUND_APP",
        message_type="CABIN_DATA",
        payload={"message": "forged-session"},
        teid=cabin_session.teid + 1,
        tag=None,
    )
    session_forged_native = native_router.forward(session_forged, now_ms=330)
    session_forged_hardened = secure_gateway.admit(session_forged, now_ms=330)
    qfi_forged = replace(
        cabin_to_avionics,
        destination_zone="GROUND_APP",
        message_type="CABIN_DATA",
        payload={"message": "forged-qfi"},
        qfi=cabin_session.qfi + 1,
        tag=None,
    )
    qfi_forged_native = native_router.forward(qfi_forged, now_ms=340)
    qfi_forged_hardened = secure_gateway.admit(qfi_forged, now_ms=340)

    maintenance_packet = _packet(
        timestamp_ms=400,
        source_id="ATG-MAINT-001",
        source_zone="GROUND_MAINT",
        destination_zone="GROUND_APP",
        dnn="atg.maint",
        session=maint_session,
        message_type="MAINTENANCE_RECORD",
        payload={"record_id": "BITE-2026-001", "status": "OPEN"},
        sequence=1,
        app_tls=True,
        secret=b"maint-key",
    )
    maintenance_native = native_router.forward(replace(maintenance_packet, tag=None), now_ms=400)
    maintenance_hardened = secure_gateway.admit(maintenance_packet, now_ms=400)

    oam_request = OamRequest(
        source="vendor_jump_host",
        role="vendor_support",
        operation="UPDATE_ROUTE_POLICY",
        target="ATG-5GC-UPF",
        config={"cabin_to_avionics_route": True, "slice_isolation": False},
        timestamp_ms=500,
        sequence=1,
        mtls=False,
        dual_approval=False,
    )
    native_oam = NativeOamController()
    native_oam_result = native_oam.apply(oam_request)
    hardened_oam = SecureOamController(secret=b"oam-key")
    hardened_oam_result = hardened_oam.apply(oam_request, now_ms=500)
    authorized_oam = make_oam_request(
        OamRequest(
            source="network_admin",
            role="network_admin",
            operation="UPDATE_ROUTE_POLICY",
            target="ATG-5GC-UPF",
            config={"cabin_to_avionics_route": False, "slice_isolation": True},
            timestamp_ms=500,
            sequence=1,
            mtls=True,
            dual_approval=True,
        ),
        b"oam-key",
    )
    authorized_oam_result = hardened_oam.apply(authorized_oam, now_ms=500)

    storm_core_native = AtgCore(authorized_ues={ue_id}, max_signaling_per_window=10)
    storm_core_hardened = AtgCore(authorized_ues={ue_id}, max_signaling_per_window=10)
    storm_native = [storm_core_native.signaling_attempt(source="rogue-ue", now_ms=0, hardened=False) for _ in range(50)]
    storm_hardened = [storm_core_hardened.signaling_attempt(source="rogue-ue", now_ms=0, hardened=True) for _ in range(50)]

    return {
        "architecture": {
            "name": "SYNTHETIC-5G-ATG",
            "plmn": SYNTHETIC_PLMN,
            "slice": SYNTHETIC_SLICE,
            "dnns": list(SYNTHETIC_DNNS),
            "qfi": 9,
            "teid_start": 0x1000,
            "chain": [
                "aircraft_5g_ue",
                "ATG NR gNB",
                "AMF/SMF/UPF dedicated core",
                "N6 ground application",
                "cabin-avionics boundary",
                "OAM/OSS management boundary",
            ],
            "dedicated_core_physical_isolation": True,
            "legacy_management_bridge_trusted": True,
            "legacy_cabin_to_avionics_acl": False,
            "hardened_cabin_to_avionics_acl": True,
        },
        "air_interface": {
            "trusted_registration": registration.__dict__,
            "rogue_cell_hardened": rogue_hardened.__dict__,
            "rogue_cell_legacy": rogue_legacy.__dict__,
            "interference": interference.__dict__,
            "events": air.events,
        },
        "normal": {
            "telemetry_native": _summary(normal_native),
            "telemetry_hardened": _summary(normal_hardened),
            "maintenance_native": _summary(maintenance_native),
            "maintenance_hardened": _summary(maintenance_hardened),
        },
        "captures": {
            "uu": capture_air,
            "n3_n6": capture_n3,
        },
        "attacks": {
            "interference": {
                "registration": interference.__dict__,
                "service_available": interference.user_plane_ready,
                "safe_degradation_required": True,
            },
            "rogue_cell": {
                "hardened": rogue_hardened.__dict__,
                "legacy": rogue_legacy.__dict__,
                "user_plane_established_by_rogue": False,
            },
            "signaling_storm": {
                "attempts": 50,
                "window_ms": 1000,
                "limit_per_window": storm_core_hardened.max_signaling_per_window,
                "native_contexts_allocated": sum(item["accepted"] for item in storm_native),
                "hardened_contexts_allocated": sum(item["accepted"] for item in storm_hardened),
                "hardened_rejected": sum(not item["accepted"] for item in storm_hardened),
                "hardened_reason": storm_hardened[-1]["reason"],
            },
            "cabin_to_avionics_boundary": {
                "input": {
                    "source_id": cabin_to_avionics.source_id,
                    "source_zone": cabin_to_avionics.source_zone,
                    "destination_zone": cabin_to_avionics.destination_zone,
                    "dnn": cabin_to_avionics.dnn,
                    "message_type": cabin_to_avionics.message_type,
                    "payload": cabin_to_avionics.payload,
                },
                "native": _summary(cabin_native),
                "hardened": _summary(cabin_hardened),
                "native_state": app_native.state,
                "hardened_state": app_secure.state,
            },
            "data_tamper": {
                "input": {
                    "original_payload": tamper_original.payload,
                    "tampered_payload": tampered.payload,
                    "timestamp_ms": tampered.timestamp_ms,
                    "sequence": tampered.sequence,
                    "original_tag_retained": tampered.tag is not None,
                },
                "native": _summary(tamper_native),
                "hardened": _summary(tamper_hardened),
            },
            "data_replay": {
                "input": {
                    "payload": replay_packet.payload,
                    "timestamp_ms": replay_packet.timestamp_ms,
                    "sequence": replay_packet.sequence,
                    "first_submission_ms": 300,
                    "replay_submission_ms": 320,
                },
                "native": _summary(replay_native),
                "first_hardened": _summary(replay_first),
                "replayed_hardened": _summary(replay_again),
            },
            "session_binding": {
                "input": {
                    "ue_id": cabin_session.ue_id,
                    "dnn": cabin_session.dnn,
                    "slice_id": cabin_session.slice_id,
                    "teid": cabin_session.teid,
                    "qfi": cabin_session.qfi,
                    "forged_teid": session_forged.teid,
                    "forged_qfi": qfi_forged.qfi,
                },
                "teid_forgery": {
                    "native": _summary(session_forged_native),
                    "hardened": _summary(session_forged_hardened),
                },
                "qfi_forgery": {
                    "native": _summary(qfi_forged_native),
                    "hardened": _summary(qfi_forged_hardened),
                },
            },
            "oam_route_change": {
                "input": {
                    "unauthorized": {
                        "source": oam_request.source,
                        "role": oam_request.role,
                        "operation": oam_request.operation,
                        "target": oam_request.target,
                        "config": oam_request.config,
                        "mtls": oam_request.mtls,
                        "dual_approval": oam_request.dual_approval,
                    },
                    "authorized": {
                        "source": authorized_oam.source,
                        "role": authorized_oam.role,
                        "operation": authorized_oam.operation,
                        "target": authorized_oam.target,
                        "config": authorized_oam.config,
                        "mtls": authorized_oam.mtls,
                        "dual_approval": authorized_oam.dual_approval,
                    },
                },
                "native": native_oam_result,
                "hardened_unauthorized": hardened_oam_result,
                "hardened_authorized": authorized_oam_result,
            },
        },
        "limitations": [
            "This is a digital 5G-ATG architecture and security-state model; it does not transmit RF, create a gNB, operate a 5GC, or use a SIM/eSIM.",
            "Cell IDs, PLMN, DNN, slice, TEID, QFI, UE IDs, application messages, and route policy are synthetic project data, not a real operator deployment.",
            "Interference is represented as RRC/NAS packet loss; no RF power, spectrum, antenna, waveform, propagation, Doppler, or jamming equipment is modeled.",
            "Rogue-cell behavior is represented as broadcast/pre-security visibility and 5G-AKA failure; it is not an operational fake-gNB or IMSI-capture implementation.",
            "The model does not prove a real aircraft avionics boundary, cabin network, 5GC, gNB, operator OAM, ground application, or airworthiness result.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_atg_scenarios(), ensure_ascii=False, indent=2, sort_keys=True))
