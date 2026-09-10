#!/usr/bin/env python3
"""Run the local civil-aviation composite simulation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.civil_aviation_platform import run_platform  # noqa: E402


def _markdown_summary(result: dict[str, object]) -> str:
    platform = result["platform"]
    integrations = result["integrations"]
    engine = result["engine"]
    traffic = result["normal_traffic"]
    attack = result["attack"]
    trace = result["trace"]
    first_state = trace[0]["state"]
    last_state = trace[-1]["state"]
    event = attack["event"]
    lines = [
        "# Civil Aviation Security Platform Experiment",
        "",
        "This record was produced by `scripts/run_civil_platform.py`. It is a local",
        "research simulation and does not connect to an aircraft, ARINC 429 interface,",
        "serial port, or network service.",
        "",
        "## Run",
        "",
        f"- Engine: `{engine}`",
        f"- Duration / sample step: `{platform['duration_ms']} ms` / `{platform['step_ms']} ms`",
        f"- ARINC labels: `{', '.join(platform['labels'])}` (synthetic project labels)",
        f"- Frames: `{traffic['frames_emitted']}` emitted, `{traffic['frames_admitted']}` admitted, `{traffic['frames_rejected']}` rejected",
        f"- Rejection reasons: `{traffic['rejection_reasons']}`",
        "",
        "## Integration inventory",
        "",
        f"- JSBSim Python: `{integrations['jsbsim_python']}` version `{integrations['jsbsim_version']}`",
        f"- BlueSky Python: `{integrations['bluesky_python']}` version `{integrations['bluesky_version']}`",
        f"- FlightGear executable: `{integrations['flightgear_binary']}`",
        f"- PX4 source present: `{integrations['px4_source']}` at `{integrations['px4_source_path']}`",
        f"- Network opened: `{integrations['network_opened']}`; serial opened: `{integrations['serial_opened']}`",
        "",
        "## Flight-state samples",
        "",
        f"- First: `{first_state}`",
        f"- Last: `{last_state}`",
        "",
        "## Attack observation",
        "",
        f"- Requested: `{attack['requested']}` at `{attack['attack_at_ms']} ms`",
        f"- Event: `{event}`",
        "",
        "## Evidence boundary",
        "",
        "JSBSim provides the flight-dynamics state only. The ARINC 429 words use",
        "synthetic labels and a local HMAC/sequence/timestamp overlay. This result",
        "does not establish a real aircraft ICD, LRU behavior, electrical waveform",
        "compliance, PX4 MAVLink HIL operation, or airworthiness approval.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--step-ms", type=int, default=1_000)
    parser.add_argument("--engine", choices=("auto", "kinematic", "jsbsim"), default="auto")
    parser.add_argument("--aircraft", default="737")
    parser.add_argument("--attack", choices=("none", "injection", "replay", "tamper"), default="none")
    parser.add_argument("--attack-at-ms", type=int, default=20_000)
    parser.add_argument("--px4-root", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "civil_platform_results.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help="optional Markdown evidence record",
    )
    args = parser.parse_args()
    result = run_platform(
        duration_ms=round(args.duration_s * 1_000),
        step_ms=args.step_ms,
        engine=args.engine,
        attack=args.attack,
        attack_at_ms=args.attack_at_ms,
        aircraft=args.aircraft,
        px4_root=args.px4_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_output = args.markdown_output or args.output.with_suffix(".md")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_markdown_summary(result), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "markdown_output": str(markdown_output),
        "engine": result["engine"],
        "normal_traffic": result["normal_traffic"],
        "attack": result["attack"],
        "integrations": result["integrations"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
