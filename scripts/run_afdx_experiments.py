#!/usr/bin/env python3
"""Run the local synthetic AFDX virtual-link laboratory."""
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.afdx_lab import run_scenarios  # noqa: E402

output = ROOT / "artifacts" / "afdx_results.json"
output.parent.mkdir(parents=True, exist_ok=True)
result = run_scenarios()
output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"output": str(output), "normal": result["normal"], "attacks": result["attacks"]}, ensure_ascii=False))
