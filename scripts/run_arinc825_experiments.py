#!/usr/bin/env python3
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from sim.arinc825_lab import run_scenarios
result=run_scenarios(); output=ROOT/'artifacts'/'arinc825_results.json'; output.parent.mkdir(exist_ok=True); output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8'); print(json.dumps({'output':str(output),'normal':result['normal'],'attacks':result['attacks']},ensure_ascii=False))
