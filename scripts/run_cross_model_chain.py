#!/usr/bin/env python3
"""Run the local cross-model semantic event chain."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.twin.chain import run_cross_model_chain

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", choices=("normal", "injection", "replay", "tamper"), default="normal")
parser.add_argument("--output", default="artifacts/cross_model_chain.json")
args = parser.parse_args()
result = run_cross_model_chain(scenario=args.scenario)
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"scenario": args.scenario, "events": len(result["events"]), "evidence": len(result["evidence"]), "terminal_status": result["terminal_status"], "output": args.output}, ensure_ascii=False))
