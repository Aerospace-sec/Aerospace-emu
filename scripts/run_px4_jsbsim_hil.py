#!/usr/bin/env python3
"""Run a local JSBSim-to-PX4 external HIL-style software co-simulation.

The bridge binds only to 127.0.0.1. It starts a PX4 POSIX/SITL binary, accepts
the simulator_mavlink TCP connection, sends HIL_SENSOR and HIL_GPS messages,
and records PX4 HIL_ACTUATOR_CONTROLS feedback. This is a software
co-simulation evidence tool, not a real flight-controller hardware test.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The repository keeps the MAVLink generator in a PX4 submodule. Make the
# bridge usable after the local build without requiring a global pymavlink.
for candidate in (
    Path("/tmp/px4-source/src/modules/mavlink/mavlink"),
    Path("/tmp/civil_aviation_jsbsim_deps"),
):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from sim.civil_aviation_platform import AircraftState, JsbsimCivilModel  # noqa: E402

try:
    from pymavlink import mavutil  # type: ignore[import-not-found]  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on local integration deps
    raise SystemExit(
        "pymavlink is required; use the PX4 submodule path or install it in an isolated environment"
    ) from exc


G = 9.80665
KT_TO_MPS = 0.5144444444444444
FPM_TO_MPS = 0.00508
SAMPLE_MS = 4
SAMPLE_HZ = 1_000 // SAMPLE_MS
HIL_SENSOR_FIELDS = (1 << 13) - 1


def _clamp_int(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _angle_delta_deg(current: float, previous: float) -> float:
    return (current - previous + 180.0) % 360.0 - 180.0


def _pressure_and_density(altitude_ft: float) -> tuple[float, float]:
    """Return standard-atmosphere pressure (Pa) and density (kg/m3)."""

    altitude_m = max(-5_000.0, min(11_000.0, altitude_ft * 0.3048))
    temperature_k = 288.15 - 0.0065 * altitude_m
    pressure_pa = 101_325.0 * (temperature_k / 288.15) ** 5.2558797
    density = pressure_pa / (287.05287 * temperature_k)
    return pressure_pa, density


def _state_summary(state: AircraftState) -> dict[str, float | int | str]:
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


def _sensor_values(
    state: AircraftState,
    previous: AircraftState | None,
) -> dict[str, float | int]:
    if previous is None:
        roll_rate = pitch_rate = yaw_rate = 0.0
        acceleration_x = 0.0
    else:
        dt = max((state.timestamp_ms - previous.timestamp_ms) / 1_000.0, 1e-6)
        roll_rate = math.radians((state.roll_deg - previous.roll_deg) / dt)
        pitch_rate = math.radians((state.pitch_deg - previous.pitch_deg) / dt)
        yaw_rate = math.radians(_angle_delta_deg(state.heading_deg, previous.heading_deg) / dt)
        acceleration_x = (
            (state.true_airspeed_kt - previous.true_airspeed_kt) * KT_TO_MPS / dt
        )

    pressure_pa, density = _pressure_and_density(state.altitude_ft)
    true_airspeed_mps = max(0.0, state.true_airspeed_kt * KT_TO_MPS)
    dynamic_pressure_hpa = 0.5 * density * true_airspeed_mps**2 / 100.0
    altitude_m = state.altitude_ft * 0.3048

    # HIL_SENSOR uses body-frame SI units. The research model does not expose
    # individual IMU axes, so the stable-flight baseline is gravity projected
    # through the JSBSim attitude plus the longitudinal finite-difference
    # acceleration. The magnetic vector is rotated with heading for a usable
    # yaw reference. This is sufficient for interface and timing validation,
    # not sensor-calibration approval.
    roll = math.radians(state.roll_deg)
    pitch = math.radians(state.pitch_deg)
    heading = math.radians(state.heading_deg)
    x_gravity = G * math.sin(pitch)
    y_gravity = -G * math.cos(pitch) * math.sin(roll)
    z_gravity = -G * math.cos(pitch) * math.cos(roll)
    magnetic_horizontal = 0.215
    return {
        "xacc": x_gravity + acceleration_x,
        "yacc": y_gravity,
        "zacc": z_gravity,
        "xgyro": roll_rate,
        "ygyro": pitch_rate,
        "zgyro": yaw_rate,
        "xmag": magnetic_horizontal * math.cos(heading),
        "ymag": -magnetic_horizontal * math.sin(heading),
        "zmag": 0.427,
        "abs_pressure": pressure_pa / 100.0,
        "diff_pressure": dynamic_pressure_hpa,
        "pressure_alt": altitude_m,
        "temperature": 15.0 - 0.0065 * max(0.0, altitude_m),
        "fields_updated": HIL_SENSOR_FIELDS,
    }


def _gps_values(state: AircraftState) -> dict[str, int]:
    heading = state.heading_deg % 360.0
    speed_mps = max(0.0, state.true_airspeed_kt * KT_TO_MPS)
    heading_rad = math.radians(heading)
    north_mps = speed_mps * math.cos(heading_rad)
    east_mps = speed_mps * math.sin(heading_rad)
    down_mps = -state.vertical_speed_fpm * FPM_TO_MPS
    return {
        "fix_type": 3,
        "lat": _clamp_int(state.latitude_deg * 1e7, -2_147_483_648, 2_147_483_647),
        "lon": _clamp_int(state.longitude_deg * 1e7, -2_147_483_648, 2_147_483_647),
        "alt": _clamp_int(state.altitude_ft * 0.3048 * 1_000.0, -2_147_483_648, 2_147_483_647),
        "eph": 100,
        "epv": 150,
        "vel": _clamp_int(speed_mps * 100.0, 0, 65_535),
        "vn": _clamp_int(north_mps * 100.0, -32_768, 32_767),
        "ve": _clamp_int(east_mps * 100.0, -32_768, 32_767),
        "vd": _clamp_int(down_mps * 100.0, -32_768, 32_767),
        "cog": _clamp_int(heading * 100.0, 0, 65_535),
        "satellites_visible": 12,
    }


def _pack(mav: Any, message: Any) -> bytes:
    return message.pack(mav)


def _send_sensor_bundle(
    conn: socket.socket,
    mav: Any,
    state: AircraftState,
    previous: AircraftState | None,
    send_gps: bool,
) -> tuple[int, int]:
    timestamp_us = state.timestamp_ms * 1_000
    sensor = _sensor_values(state, previous)
    hil_sensor = mavutil.mavlink.MAVLink_hil_sensor_message(
        timestamp_us,
        sensor["xacc"],
        sensor["yacc"],
        sensor["zacc"],
        sensor["xgyro"],
        sensor["ygyro"],
        sensor["zgyro"],
        sensor["xmag"],
        sensor["ymag"],
        sensor["zmag"],
        sensor["abs_pressure"],
        sensor["diff_pressure"],
        sensor["pressure_alt"],
        sensor["temperature"],
        sensor["fields_updated"],
    )
    conn.sendall(_pack(mav, hil_sensor))

    gps_count = 0
    if send_gps:
        gps = _gps_values(state)
        hil_gps = mavutil.mavlink.MAVLink_hil_gps_message(
            timestamp_us,
            gps["fix_type"],
            gps["lat"],
            gps["lon"],
            gps["alt"],
            gps["eph"],
            gps["epv"],
            gps["vel"],
            gps["vn"],
            gps["ve"],
            gps["vd"],
            gps["cog"],
            gps["satellites_visible"],
        )
        conn.sendall(_pack(mav, hil_gps))
        gps_count = 1
    return 1, gps_count


def _read_messages(conn: socket.socket, parser: Any, message_counts: dict[str, int]) -> list[Any]:
    messages: list[Any] = []
    while True:
        readable, _, _ = select.select([conn], [], [], 0.0)
        if not readable:
            break
        data = conn.recv(65_536)
        if not data:
            raise ConnectionError("PX4 closed the HIL connection")
        for byte in data:
            message = parser.parse_char(bytes((byte,)))
            if message is not None:
                message_type = message.get_type()
                message_counts[message_type] = message_counts.get(message_type, 0) + 1
                messages.append(message)
    return messages


def _terminate_process(process: subprocess.Popen[str], log_handle: Any) -> int | None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
    log_handle.flush()
    log_handle.close()
    return process.returncode


def _markdown_evidence(result: dict[str, Any]) -> str:
    run = result["run"]
    tx = result["tx"]
    rx = result["rx"]
    health = result["health"]
    first_state = result["states"].get("first")
    last_state = result["states"].get("last")
    lines = [
        "# PX4 + JSBSim 联仿证据记录",
        "",
        "本记录由 `scripts/run_px4_jsbsim_hil.py` 生成。测试对象是本机",
        "PX4 POSIX/SITL 软件进程与 JSBSim 737 研究模型，不是真实飞控板、",
        "ARINC 429 物理总线或实际飞机系统的验证记录。",
        "",
        "## 运行配置",
        "",
        f"- 联仿类型：`{run['kind']}`",
        f"- JSBSim 模型：`{run['jsbsim_aircraft']}`，根目录 `{run['jsbsim_root']}`",
        f"- PX4 构建：`{run['px4_binary']}`",
        f"- PX4 控制器配置：`{run['px4_airframe']}`",
        f"- 仿真时长：`{run['duration_s']} s`；传感器周期：`{run['sample_ms']} ms / {run['sample_hz']} Hz`",
        f"- 连接范围：`{run['bind_address']}`（仅回环 TCP）",
        "",
        "## 闭环结果",
        "",
        f"- PX4 已连接：`{health.get('px4_connected')}`；心跳：`{health.get('px4_heartbeat_received')}`",
        f"- 发送 `HIL_SENSOR`：`{tx.get('HIL_SENSOR', 0)}`；发送 `HIL_GPS`：`{tx.get('HIL_GPS', 0)}`",
        f"- 接收 `HIL_ACTUATOR_CONTROLS`：`{rx.get('HIL_ACTUATOR_CONTROLS', 0)}`",
        f"- PX4 返回码：`{result.get('process_returncode')}`；正常关闭：`{health.get('closed_cleanly')}`",
        f"- 首状态：`{first_state}`",
        f"- 末状态：`{last_state}`",
        "",
        "## 复现命令",
        "",
        "```bash",
        "python3 scripts/run_px4_jsbsim_hil.py \\",
        f"  --duration-s {run['duration_s']} \\",
        "  --output artifacts/px4_jsbsim_hil_results.json",
        "```",
        "",
        "## 证据边界",
        "",
    ]
    lines.extend(f"- {limitation}" for limitation in result["limitations"])
    lines.extend(
        [
            "",
            "因此，本记录可证明 JSBSim 状态、MAVLink HIL 输入和 PX4 执行器反馈在本机软件链路中完成闭环；",
            "不能证明真实飞控板 HITL、ARINC 429 电气波形、真实机型 ICD、适航符合性或运行安全。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    px4_bin = Path(args.px4_bin).resolve()
    px4_build = Path(args.px4_build).resolve()
    jsbsim_root = Path(args.jsbsim_root).resolve()
    if not px4_bin.is_file():
        raise FileNotFoundError(f"PX4 binary not found: {px4_bin}")
    if not (px4_build / "etc" / "init.d-posix" / "rcS").is_file():
        raise FileNotFoundError(f"PX4 SITL rootfs not found below: {px4_build}")
    if not (jsbsim_root / "aircraft" / args.aircraft).is_dir():
        raise FileNotFoundError(f"JSBSim aircraft model not found: {jsbsim_root / 'aircraft' / args.aircraft}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="px4-jsbsim-hil-", dir="/tmp"))
    log_path = run_dir / "px4.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process: subprocess.Popen[str] | None = None
    conn: socket.socket | None = None
    accepted_at = None
    message_counts: dict[str, int] = {}
    tx_sensor = 0
    tx_gps = 0
    actuator_samples: list[dict[str, Any]] = []
    status_text: list[str] = []
    first_state: dict[str, Any] | None = None
    last_state: dict[str, Any] | None = None
    exception_text: str | None = None

    env = os.environ.copy()
    env.update(
        {
            "PX4_SIM_MODEL": "xplane_cessna172",
            "PX4_SYS_AUTOSTART": "5001",
            "PX4_SIMULATOR": "xplane",
            "PX4_SIM_HOST_ADDR": "127.0.0.1",
            "PX4_PARAM_SYS_HITL": "1",
            "PX4_SIM_SPEED_FACTOR": "1",
            "PX4_HOME_LAT": "47.0",
            "PX4_HOME_LON": "122.0",
            "PX4_HOME_ALT": "9144.0",
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        "/tmp/px4-source/src/modules/mavlink/mavlink",
                        "/tmp/px4-python-deps",
                        "/home/starlight/.local/lib/python3.14/site-packages",
                        env.get("PYTHONPATH", ""),
                    ],
                )
            ),
        }
    )

    result: dict[str, Any] = {
        "run": {
            "kind": "PX4 SITL + JSBSim external HIL-style software co-simulation",
            "px4_binary": str(px4_bin),
            "px4_build": str(px4_build),
            "px4_airframe": "5001_xplane_cessna172 (fixed-wing controller configuration only)",
            "jsbsim_aircraft": args.aircraft,
            "jsbsim_root": str(jsbsim_root),
            "sample_ms": SAMPLE_MS,
            "sample_hz": SAMPLE_HZ,
            "duration_s": args.duration_s,
            "bind_address": f"127.0.0.1:{args.port}",
            "network_scope": "loopback only",
        },
        "tx": {"HIL_SENSOR": 0, "HIL_GPS": 0},
        "rx": {},
        "actuator_samples": actuator_samples,
        "states": {"first": None, "last": None},
        "status_text": status_text,
        "health": {},
        "limitations": [
            "PX4 is a POSIX/SITL software build, not a physical flight-control board.",
            "The PX4 5001 airframe is a fixed-wing controller configuration, not a certified Cessna or transport-aircraft model.",
            "JSBSim sensors are a research interface adapter; no ARINC 429 electrical waveform or real aircraft ICD is exercised.",
            "The TCP link is local loopback and is not evidence of an operational aircraft network path.",
        ],
    }

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", args.port))
            server.listen(1)
            server.settimeout(args.connect_timeout_s)

            process = subprocess.Popen(
                [str(px4_bin), str(px4_build / "etc")],
                cwd=run_dir,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                conn, peer = server.accept()
            except TimeoutError as exc:
                raise TimeoutError("PX4 did not connect to the local simulator bridge") from exc
            accepted_at = time.time()
            conn.setblocking(False)

            sender = mavutil.mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
            parser = mavutil.mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
            parser.robust_parsing = True
            model = JsbsimCivilModel(aircraft=args.aircraft, root_dir=jsbsim_root)
            previous: AircraftState | None = None
            start_wall = time.monotonic()
            next_tick = start_wall
            last_rx = time.monotonic()

            for index in range(int(args.duration_s * SAMPLE_HZ)):
                sim_ms = index * SAMPLE_MS
                state = model.step(sim_ms)
                if first_state is None:
                    first_state = _state_summary(state)
                last_state = _state_summary(state)

                remaining = next_tick - time.monotonic()
                while remaining > 0.0:
                    time.sleep(min(0.001, remaining))
                    remaining = next_tick - time.monotonic()
                sensor_count, gps_count = _send_sensor_bundle(
                    conn,
                    sender,
                    state,
                    previous,
                    send_gps=(index % 5 == 0),
                )
                tx_sensor += sensor_count
                tx_gps += gps_count
                previous = state
                next_tick += SAMPLE_MS / 1_000.0

                messages = _read_messages(conn, parser, message_counts)
                if messages:
                    last_rx = time.monotonic()
                for message in messages:
                    message_type = message.get_type()
                    if message_type == "HIL_ACTUATOR_CONTROLS":
                        controls = [round(float(value), 6) for value in message.controls[:8]]
                        if len(actuator_samples) < 5 or index % max(1, SAMPLE_HZ) == 0:
                            actuator_samples.append(
                                {
                                    "time_usec": int(message.time_usec),
                                    "controls_first_8": controls,
                                    "mode": int(message.mode),
                                    "flags": int(message.flags),
                                }
                            )
                    elif message_type == "STATUSTEXT" and len(status_text) < 30:
                        status_text.append(str(message.text))

                if process.poll() is not None:
                    raise RuntimeError(f"PX4 exited early with code {process.returncode}")
                if time.monotonic() - last_rx > args.rx_timeout_s and index > SAMPLE_HZ:
                    raise TimeoutError("PX4 stopped returning messages during the HIL run")

            # Drain a final short interval so the last actuator message is recorded.
            drain_end = time.monotonic() + 0.25
            while time.monotonic() < drain_end:
                for message in _read_messages(conn, parser, message_counts):
                    if message.get_type() == "HIL_ACTUATOR_CONTROLS" and len(actuator_samples) < 8:
                        actuator_samples.append(
                            {
                                "time_usec": int(message.time_usec),
                                "controls_first_8": [round(float(value), 6) for value in message.controls[:8]],
                                "mode": int(message.mode),
                                "flags": int(message.flags),
                            }
                        )
                time.sleep(0.01)
    except Exception as exc:  # record evidence and preserve the PX4 log
        exception_text = f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            conn.close()
        if process is not None:
            result["process_returncode"] = _terminate_process(process, log_handle)
        else:
            log_handle.close()

    result["tx"] = {"HIL_SENSOR": tx_sensor, "HIL_GPS": tx_gps}
    result["rx"] = message_counts
    result["states"] = {"first": first_state, "last": last_state}
    result["connection"] = {
        "accepted": accepted_at is not None,
        "peer": "127.0.0.1" if accepted_at is not None else None,
    }
    result["artifacts"] = {"px4_log": str(log_path), "run_directory": str(run_dir)}
    result["health"] = {
        "px4_connected": accepted_at is not None,
        "hil_sensor_sent": tx_sensor > 0,
        "hil_gps_sent": tx_gps > 0,
        "px4_heartbeat_received": message_counts.get("HEARTBEAT", 0) > 0,
        "px4_actuator_feedback_received": message_counts.get("HIL_ACTUATOR_CONTROLS", 0) > 0,
        "closed_cleanly": exception_text is None,
    }
    if exception_text is not None:
        result["error"] = exception_text

    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--px4-bin", default="/tmp/px4-sitl-build-full/bin/px4")
    parser.add_argument("--px4-build", default="/tmp/px4-sitl-build-full")
    parser.add_argument("--jsbsim-root", default="/tmp/civil_aviation_jsbsim_deps/jsbsim")
    parser.add_argument("--aircraft", default="737")
    parser.add_argument("--port", type=int, default=4560)
    parser.add_argument("--duration-s", type=float, default=10.0)
    parser.add_argument("--connect-timeout-s", type=float, default=20.0)
    parser.add_argument("--rx-timeout-s", type=float, default=3.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "px4_jsbsim_hil_results.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help="optional Markdown evidence record; defaults to the JSON path with .md suffix",
    )
    args = parser.parse_args()
    result = run(args)
    markdown_output = args.markdown_output or args.output.with_suffix(".md")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_markdown_evidence(result), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "markdown_output": str(markdown_output),
        "health": result["health"],
        "tx": result["tx"],
        "rx": result["rx"],
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["health"].get("px4_connected") or not result["health"].get("hil_sensor_sent"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
