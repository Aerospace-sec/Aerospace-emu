#!/usr/bin/env python3
"""Run the repository's local simulations and collect one manifest.

The orchestrator only starts deterministic local models. It does not open
network sockets, serial devices, radios, or real aircraft interfaces.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
RUN_DIR = ARTIFACTS / "one-click"


@dataclass
class RunRecord:
    name: str
    command: list[str]
    returncode: int
    duration_s: float
    stdout_log: str
    stderr_log: str


def commands(profile: str) -> list[tuple[str, list[str]]]:
    if profile not in {"quick", "full"}:
        raise ValueError(f"unsupported profile: {profile}")
    civil_duration = "10" if profile == "quick" else "180"
    return [
        ("arinc429", ["scripts/run_experiments.py"]),
        ("afdx", ["scripts/run_afdx_experiments.py"]),
        ("arinc825", ["scripts/run_arinc825_experiments.py"]),
        ("maintenance-network", ["scripts/run_maintenance_network_experiments.py"]),
        ("serial", ["scripts/run_serial_experiments.py"]),
        ("atg5g", ["scripts/run_atg5g_experiments.py"]),
        ("civil-platform", ["scripts/run_civil_platform.py", "--engine", "kinematic", "--duration-s", civil_duration]),
        ("virtual-hardware", ["scripts/run_virtual_hardware_lab.py", "--engine", "kinematic", "--duration-s", "2", "--attack", "none"]),
        ("chain-normal", ["scripts/run_cross_model_chain.py", "--scenario", "normal", "--output", "artifacts/one-click/cross_model_chain_normal.json"]),
        ("chain-injection", ["scripts/run_cross_model_chain.py", "--scenario", "injection", "--output", "artifacts/one-click/cross_model_chain_injection.json"]),
        ("render-reports", ["scripts/render_report.py"]),
    ]


def run_one(name: str, command: list[str]) -> RunRecord:
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, *command], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    duration = time.monotonic() - started
    stdout_path = RUN_DIR / f"{name}.stdout.log"
    stderr_path = RUN_DIR / f"{name}.stderr.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    print(f"[{name}] rc={completed.returncode} duration={duration:.2f}s command={shlex.join(command)}")
    return RunRecord(name, command, completed.returncode, round(duration, 3), str(stdout_path.relative_to(ROOT)), str(stderr_path.relative_to(ROOT)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "full"), default="quick")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print commands without running models")
    args = parser.parse_args(argv)
    plan = commands(args.profile)
    if args.dry_run:
        for name, command in plan:
            print(f"[{name}] {shlex.join(command)}")
        return 0
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    records: list[RunRecord] = []
    for name, command in plan:
        record = run_one(name, command)
        records.append(record)
        if record.returncode and not args.continue_on_error:
            break
    manifest = {
        "profile": args.profile,
        "simulation_only": True,
        "started_by": "scripts/run_all_simulations.py",
        "records": [asdict(item) for item in records],
        "failed": [item.name for item in records if item.returncode],
    }
    manifest_path = RUN_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path.relative_to(ROOT)), "completed": len(records), "failed": manifest["failed"]}, ensure_ascii=False))
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
