"""Small, local civil-aviation co-simulation platform.

The platform composes a deterministic civil flight scenario, an optional
JSBSim flight-dynamics adapter, an ARINC 429 sensor publisher, and the
security gateway from :mod:`sim.arinc429_lab`.

This is a research testbed. It does not claim to model a certified aircraft,
a real aircraft ICD, ARINC 429 electrical signaling, or a production crypto
architecture. No network, serial device, or external avionics interface is
opened by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version
import math
from pathlib import Path
import shutil
from typing import Callable

from .arinc429_lab import (
    AUTHORIZED_SOURCE,
    LAB_SECRET,
    Admission,
    BusFrame,
    LabelPolicy,
    LegacyReceiver,
    SecureGateway,
    decode_word,
    encode_word,
    make_secure_frame,
    tamper_data,
)


class PlatformConfigurationError(ValueError):
    """Raised when a platform option cannot be used."""


@dataclass(frozen=True)
class AircraftState:
    """State exchanged between the flight model and avionics adapter."""

    timestamp_ms: int
    phase: str
    latitude_deg: float
    longitude_deg: float
    altitude_ft: float
    true_airspeed_kt: float
    heading_deg: float
    vertical_speed_fpm: float
    pitch_deg: float
    roll_deg: float


@dataclass(frozen=True)
class CivilScenario:
    """Synthetic departure, cruise, and approach profile.

    The profile is deliberately a kinematic scenario for deterministic
    security tests. Its coordinates and values are not a real aircraft ICD.
    """

    start_latitude_deg: float = 31.1970
    start_longitude_deg: float = 121.3340

    def command_at(self, timestamp_ms: int) -> AircraftState:
        if timestamp_ms < 0:
            raise PlatformConfigurationError("timestamp_ms must be non-negative")

        t = timestamp_ms / 1000.0
        if t < 20.0:
            phase = "takeoff_climb"
            altitude = 100.0 + 60.0 * t
            airspeed = 120.0 + 1.5 * t
            vertical_speed = 3600.0
            pitch = 8.0
        elif t < 60.0:
            phase = "climb"
            altitude = 1300.0 + 50.0 * (t - 20.0)
            airspeed = 150.0 + 0.5 * (t - 20.0)
            vertical_speed = 3000.0
            pitch = 6.0
        elif t < 120.0:
            phase = "cruise"
            altitude = 3300.0
            airspeed = 170.0
            vertical_speed = 0.0
            pitch = 1.5
        elif t < 160.0:
            phase = "descent"
            altitude = 3300.0 - 30.0 * (t - 120.0)
            airspeed = 170.0
            vertical_speed = -1800.0
            pitch = -3.0
        else:
            phase = "approach"
            altitude = max(100.0, 2100.0 - 25.0 * (t - 160.0))
            airspeed = max(110.0, 170.0 - 1.0 * (t - 160.0))
            vertical_speed = -1500.0
            pitch = -2.0

        if t < 80.0:
            heading = 90.0
        else:
            heading = 90.0 + min(25.0, (t - 80.0) * 0.25)
        roll = 2.0 * math.sin(t / 12.0)
        return AircraftState(
            timestamp_ms=timestamp_ms,
            phase=phase,
            latitude_deg=self.start_latitude_deg,
            longitude_deg=self.start_longitude_deg,
            altitude_ft=altitude,
            true_airspeed_kt=airspeed,
            heading_deg=heading,
            vertical_speed_fpm=vertical_speed,
            pitch_deg=pitch,
            roll_deg=roll,
        )


class KinematicCivilModel:
    """Deterministic fallback model used when JSBSim is unavailable."""

    def __init__(self, scenario: CivilScenario | None = None) -> None:
        self.scenario = scenario or CivilScenario()
        self._last_state: AircraftState | None = None

    def step(self, timestamp_ms: int) -> AircraftState:
        command = self.scenario.command_at(timestamp_ms)
        if self._last_state is None:
            state = command
        else:
            if timestamp_ms < self._last_state.timestamp_ms:
                raise PlatformConfigurationError("flight model time cannot move backwards")
            delta_s = (timestamp_ms - self._last_state.timestamp_ms) / 1000.0
            distance_nm = self._last_state.true_airspeed_kt * delta_s / 3600.0
            heading_rad = math.radians(self._last_state.heading_deg)
            latitude = self._last_state.latitude_deg + distance_nm * math.cos(heading_rad) / 60.0
            longitude_scale = max(math.cos(math.radians(self._last_state.latitude_deg)), 0.1)
            longitude = self._last_state.longitude_deg + (
                distance_nm * math.sin(heading_rad) / (60.0 * longitude_scale)
            )
            state = replace(command, latitude_deg=latitude, longitude_deg=longitude)
        self._last_state = state
        return state


class JsbsimCivilModel:
    """Minimal optional adapter for a JSBSim Python installation.

    JSBSim aircraft data and its exact property names are installation/model
    dependent. The adapter therefore fails explicitly when a requested model
    is unavailable instead of silently claiming that the fallback is JSBSim.
    """

    def __init__(self, aircraft: str = "737", root_dir: str | Path | None = None) -> None:
        if importlib.util.find_spec("jsbsim") is None:
            raise PlatformConfigurationError(
                "JSBSim Python package is not installed; use --engine kinematic or install jsbsim"
            )
        import jsbsim  # type: ignore[import-not-found]

        if root_dir is None:
            package_root = Path(jsbsim.__file__).resolve().parent
            if (package_root / "aircraft").is_dir():
                root_dir = package_root
            else:
                try:
                    root_dir = Path(jsbsim.get_default_root_dir())
                except OSError as exc:
                    raise PlatformConfigurationError(
                        "JSBSim root directory is unavailable; pass an installation root"
                    ) from exc
        try:
            self._fdm = jsbsim.FGFDMExec(str(root_dir))
        except TypeError:
            self._fdm = jsbsim.FGFDMExec(str(root_dir))
        if hasattr(self._fdm, "set_debug_level"):
            self._fdm.set_debug_level(0)
        # JSBSim can expose optional TCP/UDP input when configured. The
        # platform is deliberately offline, so disable those channels before
        # loading the aircraft model.
        self._fdm.disable_input()
        self._fdm.disable_output()
        if not self._fdm.load_model(aircraft):
            raise PlatformConfigurationError(f"JSBSim aircraft model unavailable: {aircraft}")
        initial_file = Path(root_dir) / "aircraft" / aircraft / "cruise_init.xml"
        if initial_file.is_file():
            if not self._fdm.load_ic(str(initial_file), False):
                raise PlatformConfigurationError(f"JSBSim initial condition unavailable: {initial_file}")
        else:
            initial_conditions = {
                "ic/h-sl-ft": 33_000.0,
                "ic/mach": 0.78,
                "ic/psi-true-deg": 90.0,
                "ic/lat-gc-deg": 31.0,
                "ic/long-gc-deg": 121.334,
                "ic/flight-path-angle-deg": 0.0,
                "ic/roc-fpm": 0.0,
            }
            for name, value in initial_conditions.items():
                try:
                    self._fdm.set_property_value(name, value)
                except (KeyError, ValueError):
                    continue
        for name in (
            "propulsion/engine[0]/set-running",
            "propulsion/engine[1]/set-running",
        ):
            try:
                self._fdm.set_property_value(name, 1.0)
            except (KeyError, ValueError):
                continue
        if not self._fdm.run_ic():
            raise PlatformConfigurationError("JSBSim initial conditions could not be started")
        if hasattr(self._fdm, "do_trim"):
            try:
                # Longitudinal trim removes the large, non-representative
                # startup transient from the cruise research scenario.
                self._fdm.do_trim(0)
            except (RuntimeError, ValueError) as exc:
                raise PlatformConfigurationError("JSBSim initial-condition trim failed") from exc
        self._integration_dt = max(min(float(self._fdm.get_delta_t()), 0.02), 0.001)
        self._last_timestamp_ms: int | None = 0

    def _property(self, *names: str) -> float:
        for name in names:
            try:
                return float(self._fdm[name])
            except (KeyError, TypeError, ValueError):
                continue
        joined = ", ".join(names)
        raise PlatformConfigurationError(f"JSBSim property unavailable: {joined}")

    def step(self, timestamp_ms: int) -> AircraftState:
        if self._last_timestamp_ms is None or timestamp_ms < self._last_timestamp_ms:
            raise PlatformConfigurationError("flight model time cannot move backwards")
        remaining_s = (timestamp_ms - self._last_timestamp_ms) / 1000.0
        while remaining_s > 1e-9:
            delta_s = min(self._integration_dt, remaining_s)
            self._fdm.set_dt(delta_s)
            if not self._fdm.run():
                raise PlatformConfigurationError("JSBSim step failed")
            remaining_s -= delta_s
        self._last_timestamp_ms = timestamp_ms

        altitude_ft = self._property("position/h-sl-ft")
        vertical_speed_fpm = -self._property("velocities/v-down-fps") * 60.0
        if altitude_ft < 3_000.0 and vertical_speed_fpm > 500.0:
            phase = "takeoff_climb"
        elif vertical_speed_fpm > 500.0:
            phase = "climb"
        elif vertical_speed_fpm < -500.0 and altitude_ft < 10_000.0:
            phase = "approach"
        elif vertical_speed_fpm < -500.0:
            phase = "descent"
        else:
            phase = "cruise"

        return AircraftState(
            timestamp_ms=timestamp_ms,
            phase=phase,
            latitude_deg=self._property("position/lat-geod-deg"),
            longitude_deg=self._property("position/long-gc-deg"),
            altitude_ft=altitude_ft,
            true_airspeed_kt=self._property("velocities/vtrue-kts"),
            heading_deg=self._property("attitude/psi-deg"),
            vertical_speed_fpm=vertical_speed_fpm,
            pitch_deg=self._property("attitude/theta-deg"),
            roll_deg=self._property("attitude/phi-deg"),
        )


@dataclass(frozen=True)
class SensorDefinition:
    name: str
    label: int
    unit: str
    policy: LabelPolicy
    encode: Callable[[AircraftState], int]


def _bounded_data(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


SENSOR_DEFINITIONS: tuple[SensorDefinition, ...] = (
    SensorDefinition(
        "synthetic_altitude",
        0o203,
        "ft",
        LabelPolicy(0, 500_000, max_delta=20_000),
        lambda state: _bounded_data(state.altitude_ft, 0, 500_000),
    ),
    SensorDefinition(
        "synthetic_airspeed",
        0o210,
        "kt",
        LabelPolicy(0, 600, max_delta=150),
        lambda state: _bounded_data(state.true_airspeed_kt, 0, 600),
    ),
    SensorDefinition(
        "synthetic_heading",
        0o211,
        "deg",
        LabelPolicy(0, 360, max_delta=120),
        lambda state: _bounded_data(state.heading_deg % 360.0, 0, 360),
    ),
    SensorDefinition(
        "synthetic_vertical_speed",
        0o212,
        "fpm+5000",
        LabelPolicy(0, 10_000, max_delta=6_000),
        lambda state: _bounded_data(state.vertical_speed_fpm + 5_000.0, 0, 10_000),
    ),
)


class Arinc429SensorPublisher:
    """Encode synthetic sensor values and add the lab security overlay."""

    def __init__(self, source: str = AUTHORIZED_SOURCE, secret: bytes = LAB_SECRET) -> None:
        self.source = source
        self.secret = secret
        self._sequence = 0

    def publish(self, state: AircraftState) -> list[BusFrame]:
        frames: list[BusFrame] = []
        for sensor in SENSOR_DEFINITIONS:
            self._sequence += 1
            data = sensor.encode(state)
            raw_word = encode_word(sensor.label, 0, data, 0)
            frames.append(
                make_secure_frame(
                    state.timestamp_ms,
                    self.source,
                    raw_word,
                    self._sequence,
                    self.secret,
                )
            )
        return frames


def build_gateway(step_ms: int) -> SecureGateway:
    """Create a lab gateway sized for the configured sample interval."""

    if step_ms <= 0:
        raise PlatformConfigurationError("step_ms must be positive")
    frames_per_window = max(100, math.ceil(1_000 / step_ms) * len(SENSOR_DEFINITIONS) * 2)
    return SecureGateway(
        {AUTHORIZED_SOURCE: LAB_SECRET},
        {AUTHORIZED_SOURCE},
        {sensor.label: sensor.policy for sensor in SENSOR_DEFINITIONS},
        max_age_ms=max(3_000, step_ms * 3),
        max_future_ms=max(20, step_ms // 2),
        max_frames_per_window=frames_per_window,
        window_ms=1_000,
    )


def dependency_inventory(px4_root: str | Path | None = None) -> dict[str, object]:
    """Report optional integration points without importing or starting them."""

    candidate = Path(px4_root) if px4_root else Path("/home/starlight/winECode/PX4-Autopilot")
    def installed_version(distribution: str) -> str | None:
        try:
            return package_version(distribution)
        except PackageNotFoundError:
            return None

    return {
        "jsbsim_python": importlib.util.find_spec("jsbsim") is not None,
        "jsbsim_version": installed_version("jsbsim"),
        "bluesky_python": importlib.util.find_spec("bluesky") is not None,
        "bluesky_version": installed_version("bluesky-simulator"),
        "flightgear_binary": shutil.which("fgfs") is not None,
        "px4_source": candidate.is_dir(),
        "px4_source_path": str(candidate) if candidate.is_dir() else None,
        "network_opened": False,
        "serial_opened": False,
    }


def _make_model(
    engine: str,
    scenario: CivilScenario,
    aircraft: str,
) -> tuple[object, dict[str, object]]:
    if engine not in {"auto", "kinematic", "jsbsim"}:
        raise PlatformConfigurationError(f"unsupported engine: {engine}")
    if engine == "kinematic":
        return KinematicCivilModel(scenario), {"requested": engine, "selected": "kinematic", "aircraft": aircraft}

    try:
        model = JsbsimCivilModel(aircraft=aircraft)
        return model, {"requested": engine, "selected": "jsbsim", "aircraft": aircraft}
    except (ImportError, OSError, PlatformConfigurationError) as exc:
        if engine == "jsbsim":
            raise
        return KinematicCivilModel(scenario), {
            "requested": engine,
            "selected": "kinematic",
            "aircraft": aircraft,
            "fallback_reason": str(exc),
        }


def _frame_summary(frame: BusFrame, admission: Admission) -> dict[str, object]:
    decoded = decode_word(frame.raw_word)
    return {
        "source": frame.source,
        "label_octal": oct(decoded.label),
        "data": decoded.data,
        "raw_word": f"0x{frame.raw_word:08x}",
        "sequence": frame.sequence,
        "parity_ok": decoded.parity_ok,
        "accepted": admission.accepted,
        "reason": admission.reason,
    }


def _state_summary(state: AircraftState) -> dict[str, object]:
    return {
        "timestamp_ms": state.timestamp_ms,
        "phase": state.phase,
        "latitude_deg": round(state.latitude_deg, 7),
        "longitude_deg": round(state.longitude_deg, 7),
        "altitude_ft": round(state.altitude_ft, 2),
        "true_airspeed_kt": round(state.true_airspeed_kt, 2),
        "heading_deg": round(state.heading_deg, 2),
        "vertical_speed_fpm": round(state.vertical_speed_fpm, 2),
        "pitch_deg": round(state.pitch_deg, 2),
        "roll_deg": round(state.roll_deg, 2),
    }


def _attack_event(
    attack: str,
    timestamp_ms: int,
    current_frames: list[BusFrame],
    previous_frames: list[BusFrame] | None,
    legacy: LegacyReceiver,
    gateway: SecureGateway,
) -> dict[str, object] | None:
    if attack == "none":
        return None
    target = current_frames[0]
    if attack == "injection":
        decoded = target.decoded
        forged = BusFrame(
            timestamp_ms,
            "attacker",
            encode_word(decoded.label, decoded.sdi, decoded.data + 1_000, decoded.ssm),
        )
        legacy_result = legacy.receive(forged)
        gateway_result = gateway.admit(forged, timestamp_ms)
        return {
            "type": attack,
            "legacy": _frame_summary(forged, legacy_result),
            "hardened": _frame_summary(forged, gateway_result),
        }

    if previous_frames is None:
        return {"type": attack, "status": "not_triggered_no_previous_frame"}
    previous = previous_frames[0]
    if attack == "replay":
        replay = BusFrame(timestamp_ms, "attacker", previous.raw_word, previous.sequence, previous.tag)
        legacy_result = legacy.receive(replay)
        gateway_result = gateway.admit(previous, timestamp_ms)
        return {
            "type": attack,
            "legacy": _frame_summary(replay, legacy_result),
            "hardened": _frame_summary(previous, gateway_result),
        }

    if attack == "tamper":
        tampered = replace(previous, raw_word=tamper_data(previous.raw_word, previous.decoded.data + 1_000))
        legacy_frame = BusFrame(timestamp_ms, "attacker", tampered.raw_word)
        legacy_result = legacy.receive(legacy_frame)
        gateway_result = gateway.admit(tampered, timestamp_ms)
        return {
            "type": attack,
            "legacy": _frame_summary(legacy_frame, legacy_result),
            "hardened": _frame_summary(tampered, gateway_result),
        }

    raise PlatformConfigurationError(f"unsupported attack: {attack}")


def run_platform(
    *,
    duration_ms: int = 180_000,
    step_ms: int = 1_000,
    engine: str = "auto",
    attack: str = "none",
    attack_at_ms: int = 20_000,
    aircraft: str = "737",
    px4_root: str | Path | None = None,
) -> dict[str, object]:
    """Run the local composite platform and return JSON-serializable evidence."""

    if duration_ms <= 0:
        raise PlatformConfigurationError("duration_ms must be positive")
    if step_ms <= 0 or attack_at_ms < 0:
        raise PlatformConfigurationError("step_ms must be positive and attack_at_ms non-negative")
    if attack not in {"none", "injection", "replay", "tamper"}:
        raise PlatformConfigurationError(f"unsupported attack: {attack}")

    scenario = CivilScenario()
    model, engine_info = _make_model(engine, scenario, aircraft)
    publisher = Arinc429SensorPublisher()
    legacy = LegacyReceiver({sensor.label for sensor in SENSOR_DEFINITIONS})
    gateway = build_gateway(step_ms)
    timestamps = list(range(0, duration_ms + 1, step_ms))
    if timestamps[-1] != duration_ms:
        timestamps.append(duration_ms)

    trace: list[dict[str, object]] = []
    previous_frames: list[BusFrame] | None = None
    normal_frames = 0
    normal_admitted = 0
    rejection_reasons: dict[str, int] = {}
    attack_event: dict[str, object] | None = None
    for timestamp_ms in timestamps:
        state = model.step(timestamp_ms)  # type: ignore[attr-defined]
        frames = publisher.publish(state)
        admissions = [gateway.admit(frame, timestamp_ms) for frame in frames]
        normal_frames += len(frames)
        normal_admitted += sum(admission.accepted for admission in admissions)
        for admission in admissions:
            if not admission.accepted:
                rejection_reasons[admission.reason] = rejection_reasons.get(admission.reason, 0) + 1
        event = None
        if timestamp_ms == attack_at_ms:
            event = _attack_event(attack, timestamp_ms, frames, previous_frames, legacy, gateway)
            if event is not None:
                attack_event = event
        trace.append(
            {
                "state": _state_summary(state),
                "frames": [_frame_summary(frame, admission) for frame, admission in zip(frames, admissions)],
                "attack": event,
            }
        )
        previous_frames = frames

    result = {
        "platform": {
            "name": "civil-aviation-security-lab",
            "version": "0.1",
            "scope": "local research simulation; no real avionics interface",
            "duration_ms": duration_ms,
            "step_ms": step_ms,
            "sensor_source": AUTHORIZED_SOURCE,
            "labels": [oct(sensor.label) for sensor in SENSOR_DEFINITIONS],
        },
        "integrations": dependency_inventory(px4_root),
        "engine": engine_info,
        "normal_traffic": {
            "frames_emitted": normal_frames,
            "frames_admitted": normal_admitted,
            "frames_rejected": normal_frames - normal_admitted,
            "rejection_reasons": rejection_reasons,
        },
        "attack": {
            "requested": attack,
            "attack_at_ms": attack_at_ms,
            "event": attack_event,
        },
        "trace": trace,
    }
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(run_platform(), ensure_ascii=False, indent=2, sort_keys=True))
